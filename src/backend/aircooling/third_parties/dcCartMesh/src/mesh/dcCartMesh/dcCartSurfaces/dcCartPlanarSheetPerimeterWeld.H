#ifndef dcCartPlanarSheetPerimeterWeld_H
#define dcCartPlanarSheetPerimeterWeld_H
#include "pointField.H"
#include "triSurface.H"
#include <array>
#include <map>
#include <set>
#include <vector>
#include <cmath>
#include <algorithm>
#include <string>
#include <cstring>

namespace Foam { namespace dcCartPlanarSheetPerimeterWeld {
// Proposal only. Surface IDs/point labels/triangles are never changed.
// Caller supplies the complete collection, before shell/baffle cache creation.
struct SurfaceView
{
    label surface=-1;
    bool eligible=false; // actual internalBaffle && !carves
    const pointField* points=nullptr;
    const triSurface* triangles=nullptr;
};
using Marks=std::array<std::map<scalar,scalar>,3>;
using Classes=std::array<std::set<scalar>,3>;
using Position=std::array<scalar,3>;
using Edge=std::pair<Position,Position>;
struct AreaChange
{
    label surface=-1;
    scalar before=0, after=0;
    vector beforeVector=vector::zero, afterVector=vector::zero;
};
struct Result
{
    bool ready=false;
    Marks marks;
    std::vector<AreaChange> areaChanges;
    std::size_t proposed=0, dropped=0, sheetCount=0, perimeterEdges=0;
};
inline Position position(const point& p) { return {{p.x(),p.y(),p.z()}}; }
inline bool finite(const point& p)
{ return std::isfinite(p.x()) && std::isfinite(p.y()) && std::isfinite(p.z()); }
inline bool same(const point& a,const point& b)
{ return a.x()==b.x() && a.y()==b.y() && a.z()==b.z(); }
inline bool sameBits(const point& a,const point& b)
{
    for(direction axis=0;axis<3;++axis)
    {const scalar x=a.component(axis),y=b.component(axis);if(std::memcmp(&x,&y,sizeof(scalar))!=0)return false;}
    return true;
}
inline bool incident(const point& p,const Marks& m)
{
    for (direction a=0;a<3;++a) if (m[a].count(p.component(a))) return true;
    return false;
}
inline std::size_t count(const Marks& m)
{ return m[0].size()+m[1].size()+m[2].size(); }
inline scalar coordinate(direction a,scalar c,const Marks& old,const Marks& add)
{
    const auto n=add[a].find(c);if(n!=add[a].end())return n->second;
    const auto o=old[a].find(c);return o==old[a].end()?c:o->second;
}
inline void rejectPoint(const point& p,const Marks& add,Classes& bad)
{
    for(direction a=0;a<3;++a)
        if(add[a].count(p.component(a)))bad[a].insert(p.component(a));
}

// nearest(axis, originalCoordinate, target) returns signed target-original.
// normalFaceClasses includes ALL original axis-normal G1 candidates, including
// classes removed by the original opposing-solid/gap guard. Never reinsert them.
// legacyAt applies the unchanged original G1 map; G2 must be inactive in caller.
template<class Nearest,class LegacyAt,class Available>
bool prepare
(
    const std::vector<SurfaceView>& views,
    const Marks& legacyMarks,
    const Classes& normalFaceClasses,
    const scalar weldTol,
    const scalar onTol,
    Nearest nearest,
    LegacyAt legacyAt,
    Available available,
    Result& output,
    std::string* why=nullptr,
    const scalar seamBand=0
)
{
    output=Result();
    auto refuse=[&](const char* s){if(why)*why=s;return false;};
    if(!std::isfinite(weldTol)||weldTol<=0||!std::isfinite(onTol)||onTol<0)
        return refuse("perimeter-input");
    Result draft;
    // A SEAM is a perimeter edge two eligible sheets of one support plane
    // share: a patch inside a face (ACU_01_Front_patch beside its part1), a
    // 40 mm sealed floor passage inside the raised floor's hole. Moving it
    // only moves where one name ends and the other begins, never a free
    // edge, so it takes the wider seamBand (the first-layer floor); a free
    // perimeter keeps the classic weld band, where it could open or close a
    // gap. Counted per (support, edge) before any mark is proposed.
    using Support0=std::pair<direction,scalar>;
    std::map<std::pair<Support0,Edge>,int> seamUses;
    if(seamBand>weldTol)
    for(const auto& s:views)
    {
        if(!s.eligible||s.surface<0||!s.points||!s.triangles)continue;
        const auto& ps=*s.points;const auto& ts=*s.triangles;
        std::array<scalar,3> lo{{GREAT,GREAT,GREAT}},hi{{-GREAT,-GREAT,-GREAT}};
        std::map<Edge,unsigned> uses;bool have=false;
        forAll(ts,t)
        {
            const auto& f=ts[t];
            bool ok=true;for(label k=0;k<3;++k)if(f[k]<0||f[k]>=ps.size())ok=false;
            if(!ok)continue;
            const vector cr=(ps[f[1]]-ps[f[0]])^(ps[f[2]]-ps[f[0]]);
            if(!(magSqr(cr)>0))continue;
            have=true;
            for(label k=0;k<3;++k)
            {
                const point& p=ps[f[k]];
                for(direction axis=0;axis<3;++axis)
                {lo[axis]=std::min(lo[axis],p.component(axis));hi[axis]=std::max(hi[axis],p.component(axis));}
                Position x=position(p),y=position(ps[f[(k+1)%3]]);if(y<x)std::swap(x,y);++uses[Edge(x,y)];
            }
        }
        if(!have)continue;
        label normal=-1;bool flat=true;
        for(direction axis=0;axis<3;++axis)if(lo[axis]==hi[axis]){if(normal>=0)flat=false;normal=axis;}
        if(!flat||normal<0)continue;
        const Support0 sup(direction(normal),coordinate(direction(normal),lo[normal],legacyMarks,Marks()));
        for(const auto& e:uses)if(e.second==1)++seamUses[{sup,e.first}];
    }
    // Support plane -> complete coordinate classes. All actual axis-normal
    // triangles are barriers, including noneligible surfaces and unmarked coords.
    // Normal-axis legacy shifts are composed before grouping support planes.
    using Support=std::pair<direction,scalar>;
    std::map<Support,Classes> barriers;
    std::set<Support> eligibleSupports;
    for(const auto& s:views)
    {
        if(!available())return refuse("clock");
        if(s.surface<0||!s.points||!s.triangles)return refuse("perimeter-inventory");
        const auto& ps=*s.points;const auto& ts=*s.triangles;
        for(const point& p:ps)if(!finite(p))return refuse("perimeter-point");
        bool whole=true,have=false;
        std::array<scalar,3> lo{{GREAT,GREAT,GREAT}},hi{{-GREAT,-GREAT,-GREAT}};
        std::map<Edge,std::pair<unsigned,int>> edges;
        forAll(ts,t)
        {
            if(!available())return refuse("clock");
            const auto& f=ts[t];
            for(label k=0;k<3;++k)
                if(f[k]<0||f[k]>=ps.size())return refuse("perimeter-face");
            const point& a=ps[f[0]];const point& b=ps[f[1]];const point& c=ps[f[2]];
            const vector cr=(b-a)^(c-a);
            if(!finite(cr))return refuse("perimeter-degenerate-input");
            // Existing finite zero triangles are not outline witnesses. Their
            // staged coordinates must later remain bitwise unchanged.
            if(!(magSqr(cr)>0)){whole=false;continue;}
            for(direction axis=0;axis<3;++axis)
            {
                if(a.component(axis)==b.component(axis)&&a.component(axis)==c.component(axis))
                {
                    const Support key(axis,coordinate(axis,a.component(axis),legacyMarks,Marks()));
                    auto& all=barriers[key];
                    for(direction q=0;q<3;++q)
                    {
                        all[q].insert(a.component(q));all[q].insert(b.component(q));all[q].insert(c.component(q));
                    }
                }
            }
            if(!s.eligible)continue;
            have=true;
            for(label k=0;k<3;++k)
            {
                const point& p=ps[f[k]];
                for(direction axis=0;axis<3;++axis)
                {lo[axis]=std::min(lo[axis],p.component(axis));hi[axis]=std::max(hi[axis],p.component(axis));}
                Position x=position(p),y=position(ps[f[(k+1)%3]]);
                const bool forward=x<y;if(!forward)std::swap(x,y);
                auto& e=edges[Edge(x,y)];++e.first;e.second+=forward?1:-1;
            }
        }
        if(!s.eligible||!have)continue;
        label normal=-1;
        for(direction axis=0;axis<3;++axis)
            if(lo[axis]==hi[axis]) {if(normal>=0){whole=false;break;}normal=axis;}
        if(!whole||normal<0)continue; // whole curved sheets produce no marks
        for(const auto& e:edges)
            if(e.second.first>2||(e.second.first==2&&e.second.second!=0)) {whole=false;break;}
        if(!whole)continue; // malformed topology is not a perimeter witness
        eligibleSupports.insert(Support(direction(normal),coordinate(direction(normal),lo[normal],legacyMarks,Marks())));
        ++draft.sheetCount;
        for(const auto& e:edges)
        {
            if(e.second.first!=1)continue;
            ++draft.perimeterEdges;
            // Multiple touching loops / degree-four original nodes are allowed.
            for(direction axis=0;axis<3;++axis)
            {
                if(axis==normal||e.first.first[axis]!=e.first.second[axis])continue;
                const scalar c=e.first.first[axis];
                if(normalFaceClasses[axis].count(c)||legacyMarks[axis].count(c))continue;
                scalar target=0;const scalar d=nearest(axis,c,target);
                if(d==GREAT)continue;
                if(!std::isfinite(d)||!std::isfinite(target)||d!=target-c)return refuse("perimeter-nearest");
                const scalar shift=target-c;
                const auto seam=seamUses.find({Support0(direction(normal),coordinate(direction(normal),lo[normal],legacyMarks,Marks())),e.first});
                const scalar band=(seam!=seamUses.end()&&seam->second>=2)?seamBand:weldTol;
                if(!(std::abs(shift)>onTol)||std::abs(shift)>band)continue;
                const auto inserted=draft.marks[axis].emplace(c,target);
                if(!inserted.second&&inserted.first->second!=target)return refuse("perimeter-target-conflict");
            }
        }
    }
    draft.proposed=count(draft.marks);
    // Each rejecting iteration removes at least one NEW class. No legacy mark,
    // point, source or candidate target is rewritten on a rejected iteration.
    for(std::size_t iteration=0;iteration<=draft.proposed;++iteration)
    {
        if(!available())return refuse("clock");
        Classes bad;
        auto mapped=[&](const point& p)
        {
            point next=legacyAt(p);
            for(direction axis=0;axis<3;++axis)
            {const auto it=draft.marks[axis].find(p.component(axis));if(it!=draft.marks[axis].end())next.component(axis)=it->second;}
            return next;
        };
        for(const Support& key:eligibleSupports)
        {
            const auto found=barriers.find(key);
            if(found==barriers.end())return refuse("perimeter-support-inventory");
            for(direction axis=0;axis<3;++axis)
            {
                if(axis==key.first||draft.marks[axis].empty())continue;
                const auto& cs=found->second[axis];
                bool have=false;scalar previous=0,previousOriginal=0;
                for(const scalar c:cs)
                {
                    const scalar value=coordinate(axis,c,legacyMarks,draft.marks);
                    // A new mark may land ON a coordinate that is already
                    // there (a seal patch's edge 0.7 mm off the rack boundary
                    // the legacy weld put on the grid plane: Shinaide_liquid
                    // kept a 0.7 mm slab of floor otherwise); the triangle
                    // test below still refuses any sheet that would fold or
                    // lose area. Two NEW marks meeting, or any crossing, is
                    // refused here.
                    const bool tie=have&&previous==value
                        &&!(draft.marks[axis].count(previousOriginal)&&draft.marks[axis].count(c));
                    if(have&&!(previous<value)&&!tie)
                    {
                        // Existing legacy-only merges remain unchanged.
                        if(getenv("DCCART_WELD_DEBUG"))Foam::Info<<"  perimeter order clash axis "<<label(axis)<<" "<<previousOriginal<<" / "<<c<<" support "<<label(key.first)<<Foam::nl;
                        if(draft.marks[axis].count(previousOriginal))bad[axis].insert(previousOriginal);
                        if(draft.marks[axis].count(c))bad[axis].insert(c);
                    }
                    previous=value;previousOriginal=c;have=true;
                }
            }
        }
        for(const auto& s:views)
        {
            const auto& ps=*s.points;const auto& ts=*s.triangles;
            forAll(ts,t)
            {
                if(!available())return refuse("clock");
                const auto& f=ts[t];
                const point& a=ps[f[0]];const point& b=ps[f[1]];const point& c=ps[f[2]];
                const vector original=(b-a)^(c-a);
                const bool originalZero=!(magSqr(original)>0);
                if(!originalZero&&!incident(a,draft.marks)&&!incident(b,draft.marks)&&!incident(c,draft.marks))continue;
                const point na=mapped(a),nb=mapped(b),nc=mapped(c);
                if(originalZero)
                {
                    const point oa=legacyAt(a),ob=legacyAt(b),oc=legacyAt(c);
                    if(!finite(oa)||!finite(ob)||!finite(oc))return refuse("perimeter-legacy-point");
                    if(sameBits(na,oa)&&sameBits(nb,ob)&&sameBits(nc,oc))continue;
                    // No NEW proposal may move an existing zero triangle,
                    // including translations which leave its area zero.
                    rejectPoint(a,draft.marks,bad);rejectPoint(b,draft.marks,bad);rejectPoint(c,draft.marks,bad);
                    continue;
                }
                const vector changed=(nb-na)^(nc-na);
                const scalar sense=original&changed;
                if(!finite(na)||!finite(nb)||!finite(nc)||!finite(changed)||!std::isfinite(sense)||!(magSqr(changed)>0)||!(sense>0))
                {
                    // Withdraw the farthest move first: the strip of floor
                    // between two seal patches folds when both its edges go
                    // to one plane, and dropping both left a 0.7 mm slab the
                    // nearer move alone removes. The loop retries the rest.
                    direction ax=0;scalar cc=0,best=-1;
                    for(const point* q:{&a,&b,&c})for(direction k=0;k<3;++k)
                    {
                        const auto it=draft.marks[k].find(q->component(k));
                        if(it==draft.marks[k].end())continue;
                        const scalar sh=std::abs(it->second-it->first);
                        if(sh>best||(sh==best&&(k<ax||(k==ax&&it->first<cc)))){best=sh;ax=k;cc=it->first;}
                    }
                    if(best>=0)bad[ax].insert(cc);
                }
            }
        }
        const std::size_t before=count(draft.marks);
        if(getenv("DCCART_WELD_DEBUG"))for(direction axis=0;axis<3;++axis)for(scalar c:bad[axis])
            Foam::Info<<"  perimeter guard drop axis "<<label(axis)<<" coord "<<c<<" iteration "<<label(iteration)<<Foam::nl;
        for(direction axis=0;axis<3;++axis)for(scalar c:bad[axis])draft.marks[axis].erase(c);
        const std::size_t after=count(draft.marks);draft.dropped+=before-after;
        if(after!=before)continue;
        for(const auto& s:views)
        {
            if(!available())return refuse("clock");
            const auto& ps=*s.points;const auto& ts=*s.triangles;
            bool touched=false;
            for(const point& p:ps)if(incident(p,draft.marks)){touched=true;break;}
            if(!touched)continue;
            AreaChange change;change.surface=s.surface;
            forAll(ts,t)
            {
                if(!available())return refuse("clock");
                const auto& f=ts[t];const point& a=ps[f[0]];const point& b=ps[f[1]];const point& c=ps[f[2]];
                const vector oldArea=scalar(0.5)*((b-a)^(c-a));
                const point na=mapped(a),nb=mapped(b),nc=mapped(c);
                const vector newArea=scalar(0.5)*((nb-na)^(nc-na));
                change.before+=mag(oldArea);change.after+=mag(newArea);
                change.beforeVector+=oldArea;change.afterVector+=newArea;
            }
            if(!std::isfinite(change.before)||!std::isfinite(change.after)||!finite(change.beforeVector)||!finite(change.afterVector))return refuse("perimeter-area");
            draft.areaChanges.push_back(change);
        }
        draft.ready=true;output=std::move(draft);if(why)why->clear();return true;
    }
    return refuse("perimeter-guard-progress");
}
} }
#endif
