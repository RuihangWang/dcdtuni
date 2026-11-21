#ifndef dcCartSharedAxisColumnCompound_H
#define dcCartSharedAxisColumnCompound_H
#include "dcCartSharedAxisSeamVolumePartition.H"
#include "dcCartFiniteOutsidePromotionMetadata.H"
#include <tuple>
#include <array>

namespace Foam { namespace dcCartSharedAxisColumnCompound
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace Finite=dcCartFiniteTensorCutScope;
using Plane=std::pair<label,scalar>;
using RootPlanes=std::map<label,std::vector<Plane>>;
// Observation only: actual new finite coupled requests, never source authority.
struct SourceClosureObservation
{
    static constexpr std::size_t capacity=256;
    struct Edge
    {label root=-1,containingFace=-1,processorFace=-1,a=-1,b=-1;Plane plane;};
    std::array<Edge,capacity> edges;
    std::size_t stored=0,omitted=0;
    void clear()noexcept{stored=omitted=0;}
    void note(label c,label f,label g,label a,label b,const Plane& p)noexcept
    {if(stored<capacity)edges[stored++]={c,f,g,a,b,p};else ++omitted;}
};
struct SourceSeamCache
{
    const void* rawIdentity=nullptr;
    std::map<label,std::vector<Plane>> seams;
};
// Match the original all-face scan: ascending face IDs and two incidences
// for a self endpoint. This index contains topology only, never gate results.
inline void updateCellFaces(std::vector<std::vector<label>>& cf,label face,
 label beforeOwner,label beforeNeighbour,bool beforeAlive,
 label afterOwner,label afterNeighbour,bool afterAlive)
{
    auto change=[&](label endpoint,bool insert)
    {
        if(endpoint<0)return;
        auto& faces=cf[endpoint];const auto at=std::lower_bound(faces.begin(),faces.end(),face);
        if(insert)faces.insert(at,face);
        else if(at!=faces.end()&&*at==face)faces.erase(at);
    };
    if(beforeAlive){change(beforeOwner,false);change(beforeNeighbour,false);}
    if(afterAlive){change(afterOwner,true);change(afterNeighbour,true);}
}
// A prepare-local index. Existing point coordinates and cell liveness are
// immutable in this transaction; births/cells are append-only and every changed
// face is presented below with its actual old and new complete ring. No gate,
// source permission, geometric proof or immutable raw index is cached here.
class DraftPointIndex
{
    S::PointIndex index_;
    bool ready_=false;
public:
    const S::PointIndex& get()const{return index_;}
    template<class Mesh>
    static bool active(const Mesh& m,label face)
    {
        const label o=m.owner[face],n=m.neighbour[face];
        return m.faceAlive[face]&&((o>=0&&o<m.nCells&&m.cellAlive[o])
            ||(n>=0&&n<m.nCells&&m.cellAlive[n]));
    }
    template<class Mesh>
    bool ensure(const Mesh& m,const std::function<bool()>& available)
    {
        if(ready_)return S::clock(available);
        try{ready_=S::pointIndex(m,available,index_);return ready_;}
        catch(...){return false;}
    }
    template<class Mesh>
    bool replaceFace(const Mesh& m,label face,const std::vector<label>& beforeRing,
        bool beforeActive,const std::function<bool()>& available)
    {
        if(!ready_||!S::clock(available)||face<0||std::size_t(face)>=m.faces.size()
            ||m.points.size()<index_.faces.size()
            ||m.points.size()>std::size_t(std::numeric_limits<label>::max()))return false;
        try
        {
            index_.faces.resize(m.points.size());
            index_.initialPoints=label(m.points.size());
            auto change=[&](label p,bool insert)
            {
                if(!S::clock(available)||p<0||p>=index_.initialPoints)return false;
                auto& incidence=index_.faces[p];
                const auto at=std::lower_bound(incidence.begin(),incidence.end(),face);
                if(!insert)
                {
                    if(at==incidence.end()||*at!=face)return false;
                    incidence.erase(at);
                    if(incidence.empty())
                    {
                        S::GridKey key;if(!S::gridKey(m.points[p],key))return false;
                        const auto found=index_.grid.find(key);
                        if(found==index_.grid.end())return false;
                        auto& bucket=found->second;
                        const auto labelAt=std::lower_bound(bucket.begin(),bucket.end(),p);
                        if(labelAt==bucket.end()||*labelAt!=p)return false;
                        bucket.erase(labelAt);if(bucket.empty())index_.grid.erase(found);
                    }
                }
                else
                {
                    const bool wasEmpty=incidence.empty();incidence.insert(at,face);
                    if(wasEmpty)
                    {
                        if(!S::finite(m.points[p]))return false;
                        S::GridKey key;if(!S::gridKey(m.points[p],key))return false;
                        auto& bucket=index_.grid[key];
                        const auto labelAt=std::lower_bound(bucket.begin(),bucket.end(),p);
                        if(labelAt!=bucket.end()&&*labelAt==p)return false;
                        bucket.insert(labelAt,p);
                    }
                }
                return true;
            };
            if(beforeActive)for(label p:beforeRing)if(!change(p,false))return false;
            if(active(m,face))for(label p:m.faces[face])if(!change(p,true))return false;
            return S::clock(available);
        }
        catch(...){return false;}
    }
};
struct SparseMesh
{
    std::vector<point> points;
    std::vector<std::vector<label>> faces;
    std::vector<label> owner,neighbour,patch,zone,cellRank;
    std::vector<unsigned char> faceAlive,cellAlive;
    label nCells=0;
};
// Only actual selected roots and their complete immediate outside shells are
// copied. Native point/face/root identity is restored before returning a plan.
// Coupled permission is not provided here: the caller must supply an explicit
// old matched source role, exchange final subrings/CCV and validate both ends.
template<class Mesh>
bool sourceClosure(const Mesh& raw,const std::vector<std::vector<label>>& cf,
 const std::vector<label>& seeds,const std::vector<Plane>& requested,label rank,
 const S::PointIndex& rawIndex,const std::function<bool(label)>& blockedSource,
 const std::function<bool(label)>& selectedAllowed,const std::function<bool()>& available,
 RootPlanes& out,std::string* why=nullptr,std::vector<label>* attempted=nullptr,
 const RootPlanes* requestedRoots=nullptr,SourceSeamCache* seamCache=nullptr,
 Finite::CutScope cutScope=Finite::CutScope::Propagating,
 Finite::SurfaceHalo* completedHalo=nullptr,
 const std::function<bool(label)>& actualCoupled={},
 SourceClosureObservation* observation=nullptr,
 dcCartFiniteOutsidePromotionMetadata::Recorder* outsidePromotion=nullptr,
 const std::vector<Finite::SurfaceEdgeBirth>* surfaceBirthRequests=nullptr)
{
    out.clear();if(attempted)attempted->clear();
    if(outsidePromotion)outsidePromotion->reset();
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    const bool surfaceOnly=surfaceBirthRequests&&!surfaceBirthRequests->empty()
        &&cutScope==Finite::CutScope::FiniteTensor;
    if(((!surfaceOnly)&&(seeds.empty()||requested.empty()))||cf.size()<std::size_t(raw.nCells)
        ||rawIndex.initialPoints!=label(raw.points.size()))return fail("compound-input");
    const auto work=[&](){return S::clock(available);};
    const bool finiteCut=cutScope==Finite::CutScope::FiniteTensor;
    if((cutScope!=Finite::CutScope::Propagating&&!finiteCut)
        ||(finiteCut&&(!actualCoupled||!completedHalo)))return fail("finite-scope-input");
    if(completedHalo)*completedHalo=Finite::SurfaceHalo();
    Finite::SurfaceHalo halo;
    // Each plane belongs to the real root that triggered it. A crossed face
    // or full crossed-edge star propagates that same plane to its incidences;
    // A newly reached root receives only those requested or propagated planes.
    RootPlanes assigned,initial;
    if(requestedRoots)initial=*requestedRoots;
    else for(label c:seeds)initial[c]=requested;
    if(seamCache&&seamCache->rawIdentity!=static_cast<const void*>(&raw))
    {seamCache->seams.clear();seamCache->rawIdentity=static_cast<const void*>(&raw);}
    std::set<label> selected,validated;
    std::set<std::pair<label,Plane>> queued;
    std::vector<std::pair<label,Plane>> queue;
    std::set<std::tuple<label,scalar,label,label>> expanded;
    auto alive=[&](label c){return c>=0&&c<raw.nCells&&raw.cellAlive[c];};
    auto enqueue=[&](label c,const Plane& p)
    {
        if(queued.emplace(c,p).second)queue.emplace_back(c,p);
    };
    for(const auto& entry:initial)
        for(const auto& p:entry.second)
        {
            if(p.first<0||p.first>2||!std::isfinite(p.second))return fail("compound-plane");
            enqueue(entry.first,p);
        }
    if(queue.empty()&&!surfaceOnly)return fail("compound-input");
    if(finiteCut&&initial.size()>Finite::volumeParentCap)return fail("finite-volume-cap");
    const char* haloDetail="finite-halo-incidence";
    auto haloParent=[&](label c)
    {
        if(c<0)return true;
        if(!alive(c)){haloDetail="finite-halo-parent-not-alive";return false;}
        if(raw.cellRank[c]!=rank){haloDetail="finite-halo-parent-rank";return false;}
        if(cf[c].size()<4||cf[c].size()>64)
        {haloDetail="finite-halo-parent-cf-cap";return false;}
        halo.parents.insert(c);
        if(halo.parents.size()>Finite::haloParentCap)
        {haloDetail="finite-halo-parent-cap";return false;}
        return true;
    };
    auto haloFace=[&](label f)
    {
        if(!work()){haloDetail="finite-halo-clock";return false;}
        if(f<0||std::size_t(f)>=raw.faces.size())
        {haloDetail="finite-halo-face-label";return false;}
        if(!raw.faceAlive[f]){haloDetail="finite-halo-face-not-alive";return false;}
        if(raw.faces[f].size()<3||raw.faces[f].size()>Finite::ringCap)
        {haloDetail="finite-halo-face-ring-cap";return false;}
        halo.faces.insert(f);
        if(halo.faces.size()>Finite::surfaceFaceCap)
        {haloDetail="finite-halo-face-cap";return false;}
        if(!haloParent(raw.owner[f]))return false;
        return haloParent(raw.neighbour[f]);
    };
    const auto declaredOnPlane=[&](label c,const Plane& plane)
    {
        const auto root=initial.find(c);
        return root!=initial.end()&&std::find(root->second.begin(),root->second.end(),plane)!=root->second.end();
    };
    auto noteSurfaceEdge=[&](label root,label containing,label g,const S::Edge& edge,const Plane& plane)
    {
        if(declaredOnPlane(raw.owner[g],plane)||declaredOnPlane(raw.neighbour[g],plane)
            ||!actualCoupled(g))return true;
        S::Birth birth;if(!S::exactSurfaceEdgeBirth(raw,edge.first,edge.second,plane,available,birth))return false;
        Finite::SurfaceEdgeBirth intent;intent.source=g;intent.root=root;intent.containingFace=containing;
        intent.first=edge.first;intent.second=edge.second;intent.plane=plane;
        intent.firstBits=Finite::pointBits(raw.points[edge.first]);
        intent.secondBits=Finite::pointBits(raw.points[edge.second]);intent.birthBits=Finite::pointBits(birth.position);
        auto& list=halo.coupledEdgeBirths[g];
        for(const auto& old:list)if(old.first==intent.first&&old.second==intent.second&&old.plane==intent.plane)
            return old.firstBits==intent.firstBits&&old.secondBits==intent.secondBits&&old.birthBits==intent.birthBits;
        list.push_back(std::move(intent));return list.size()<=Finite::pointCap;
    };
    // Incoming requests refine only their real old edge-star. No queue/enqueue
    // operation is permitted here, even if the plane crosses an incident cell.
    if(surfaceOnly&&surfaceBirthRequests->size()>Finite::surfaceFaceCap)return fail("surface-edge-request-cap");
    if(surfaceOnly)for(const auto& intent:*surfaceBirthRequests)
    {
        if(!work()||intent.source<0||std::size_t(intent.source)>=raw.faces.size()
            ||!raw.faceAlive[intent.source]||!actualCoupled(intent.source))return fail("surface-edge-qualified-source");
        const auto first=intent.firstBits<intent.secondBits?intent.firstBits:intent.secondBits;
        const auto second=intent.firstBits<intent.secondBits?intent.secondBits:intent.firstBits;
        Finite::SurfaceEdgeBirth resolved;
        if(!S::resolveSurfaceEdgeBirth(raw,intent.source,first,second,intent.birthBits,
            intent.plane,available,resolved)||resolved.first!=intent.first||resolved.second!=intent.second
            ||resolved.firstBits!=intent.firstBits||resolved.secondBits!=intent.secondBits)
            return fail("surface-edge-receive-preimage");
        const S::Edge edge{intent.first,intent.second};bool sourceSeen=false;
        for(label g:rawIndex.faces[edge.first])
        {
            if(!work()||g<0||std::size_t(g)>=raw.faces.size()||!raw.faceAlive[g])
                return fail("surface-edge-star");
            bool contains=false;
            for(std::size_t k=0;k<raw.faces[g].size();++k)
                contains=contains||S::Edge(std::minmax(raw.faces[g][k],
                    raw.faces[g][(k+1)%raw.faces[g].size()]))==edge;
            if(!contains)continue;if(g==intent.source)sourceSeen=true;
            if(blockedSource&&blockedSource(g))return fail("compound-sensitive-source");
            if(!haloFace(g))return fail(haloDetail);
            if(outsidePromotion)outsidePromotion->record
                (g,raw.owner[g],raw.neighbour[g],edge.first,edge.second,intent.plane,initial);
            if(!noteSurfaceEdge(intent.root,intent.source,g,edge,intent.plane))return fail("surface-edge-discovery");
        }
        if(!sourceSeen)return fail("surface-edge-star-source");
    }
    for(std::size_t j=0;j<queue.size();++j)
    {
        if(!work())return fail("clock");
        const label c=queue[j].first;const Plane plane=queue[j].second;
        if(!alive(c)||raw.cellRank[c]!=rank||cf[c].size()<4||cf[c].size()>64)
            return fail("compound-parent");
        if(!validated.count(c))
        {
            for(label f:cf[c])
            {
                if(f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]
                    ||(raw.owner[f]!=c&&raw.neighbour[f]!=c)
                    ||raw.faces[f].size()<3||raw.faces[f].size()>64)
                    return fail("compound-incidence");
                if(blockedSource&&blockedSource(f))return fail("compound-sensitive-source");
                for(label p:raw.faces[f])
                    if(p<0||std::size_t(p)>=raw.points.size()||!S::finite(raw.points[p]))
                        return fail("compound-point");
            }
            validated.insert(c);
        }
        bool pos=false,neg=false;
        for(label f:cf[c])for(label p:raw.faces[f])
        {pos=pos||raw.points[p][plane.first]>plane.second;
         neg=neg||raw.points[p][plane.first]<plane.second;}
        if(!pos||!neg)return fail("compound-not-crossed");
        if(selectedAllowed&&!selectedAllowed(c))return fail("prior-partition");
        selected.insert(c);assigned[c].push_back(plane);
        for(label f:cf[c])
        {
            if(finiteCut&&!haloFace(f))return fail(haloDetail);
            if(S::mixed(raw,raw.faces[f],plane.first,plane.second))
            {
                const label n=raw.owner[f]==c?raw.neighbour[f]:raw.owner[f];
                if(!finiteCut&&alive(n))enqueue(n,plane);
            }
            for(std::size_t k=0;k<raw.faces[f].size();++k)
            {
                const label a=raw.faces[f][k],b=raw.faces[f][(k+1)%raw.faces[f].size()];
                const scalar x=raw.points[a][plane.first],y=raw.points[b][plane.first];
                if(!((x<plane.second&&y>plane.second)||(y<plane.second&&x>plane.second)))continue;
                const auto edge=std::minmax(a,b);
                if(!expanded.emplace(plane.first,plane.second,edge.first,edge.second).second)continue;
                if(std::size_t(edge.first)>=rawIndex.faces.size())return fail("compound-edge-index");
                for(label g:rawIndex.faces[edge.first])
                {
                    if(!work())return fail("clock");bool contains=false;
                    for(std::size_t k=0;k<raw.faces[g].size();++k)
                        contains=contains||S::Edge(std::minmax(raw.faces[g][k],
                            raw.faces[g][(k+1)%raw.faces[g].size()]))==S::Edge(edge);
                    if(!contains)continue;
                    if(finiteCut)
                    {
                        if(!haloFace(g))return fail(haloDetail);
                        if(outsidePromotion)outsidePromotion->record
                            (g,raw.owner[g],raw.neighbour[g],edge.first,edge.second,plane,initial);
                        const auto declaredOnPlane=[&](label c)
                        {
                            const auto root=initial.find(c);
                            return root!=initial.end()&&std::find(root->second.begin(),
                                root->second.end(),plane)!=root->second.end();
                        };
                        const bool declared=declaredOnPlane(raw.owner[g])||declaredOnPlane(raw.neighbour[g]);
                        if(!declared&&actualCoupled(g))
                        {
                            // Finite queue contains only the value-owned initial
                            // requests: faces x those distinct planes bounds this
                            // deduplicated discovery inventory, without a new quota.
                            if(!noteSurfaceEdge(c,f,g,S::Edge(edge),plane))
                                return fail("surface-edge-discovery");
                            auto& required=halo.coupledCuts[g];
                            if(std::find(required.begin(),required.end(),plane)==required.end())
                            {
                                required.push_back(plane);
                                if(observation)observation->note(c,f,g,edge.first,edge.second,plane);
                            }
                        }
                    }
                    else for(label n:{raw.owner[g],raw.neighbour[g]})if(alive(n))enqueue(n,plane);
                }
            }
        }
    }
    if(attempted)attempted->assign(selected.begin(),selected.end());
    for(auto& entry:assigned)
    {
        std::sort(entry.second.begin(),entry.second.end());
        entry.second.erase(std::unique(entry.second.begin(),entry.second.end()),entry.second.end());
    }
    if(!work())return fail("clock");
    if(finiteCut)
    {
        std::set<label> points;
        for(label c:halo.parents)for(label f:cf[c])
        {
            if(!work()||f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f]
                ||(raw.owner[f]!=c&&raw.neighbour[f]!=c)||raw.faces[f].size()<3
                ||raw.faces[f].size()>Finite::ringCap)return fail("finite-context-incidence");
            for(label p:raw.faces[f])
            {
                if(p<0||std::size_t(p)>=raw.points.size()||!S::finite(raw.points[p]))return fail("finite-context-point");
                points.insert(p);if(points.size()>Finite::pointCap)return fail("finite-point-cap");
            }
        }
        *completedHalo=std::move(halo);
    }
    out=std::move(assigned);return true;
}
// After the uniform request fixed point, run this complete sparse geometry
// transaction once. sourceClosure is re-read here with live selection/clock gates.
template<class Mesh>
bool prepare(const Mesh& raw,const std::vector<std::vector<label>>& cf,
 const std::vector<label>& seeds,const std::vector<Plane>& requested,label rank,
 const S::PointIndex& rawIndex,const std::function<bool(label)>& blockedSource,
 const std::function<bool(label)>& selectedAllowed,const std::function<bool()>& available,
 S::Plan& out,std::string* why=nullptr,std::vector<label>* attempted=nullptr,
 std::vector<Plane>* completedPlanes=nullptr,const RootPlanes* requestedRoots=nullptr,
 RootPlanes* completedRoots=nullptr,SourceSeamCache* seamCache=nullptr,
 bool preserveConvexSourceRings=false,
 Finite::CutScope cutScope=Finite::CutScope::Propagating,
 Finite::SurfaceHalo* completedHalo=nullptr,
  const std::function<bool(label)>& actualCoupled={},
  const std::vector<Finite::SurfaceEdgeBirth>* surfaceBirthRequests=nullptr)
{
    out=S::Plan();
    auto fail=[&](const char* s){if(why)*why=s;return false;};
    const auto work=[&](){return S::clock(available);};
    const bool finiteCut=cutScope==Finite::CutScope::FiniteTensor;
    RootPlanes assigned;Finite::SurfaceHalo halo;
    if(!sourceClosure(raw,cf,seeds,requested,rank,rawIndex,blockedSource,
        selectedAllowed,available,assigned,why,attempted,requestedRoots,seamCache,
        cutScope,finiteCut?&halo:nullptr,actualCoupled,nullptr,nullptr,surfaceBirthRequests))return false;
    // Discovery may report requests, but geometry cannot consume an unresolved
    // processor halo or treat an endpoint-only request as a volume cut.
    if(finiteCut&&!halo.coupledEdgeBirths.empty())
    {
        if(!surfaceBirthRequests)return fail("finite-unresolved-coupled-halo");
        for(const auto& group:halo.coupledEdgeBirths)for(const auto& need:group.second)
        {
            bool found=false;
            for(const auto& have:*surfaceBirthRequests)
                found=found||(have.source==need.source&&have.first==need.first&&have.second==need.second
                    &&have.plane==need.plane&&have.firstBits==need.firstBits
                    &&have.secondBits==need.secondBits&&have.birthBits==need.birthBits);
            if(!found)return fail("finite-unresolved-coupled-edge-birth");
        }
    }
    S::Plan surfacePlan;
    const bool hasSurface=finiteCut&&surfaceBirthRequests&&!surfaceBirthRequests->empty();
    if(hasSurface&&!S::prepareSurfaceEdgeBirths(raw,cf,rank,rawIndex,*surfaceBirthRequests,
        blockedSource,available,surfacePlan,why))return false;
    std::set<label> selected;std::vector<Plane> planes;
    for(const auto& entry:assigned)
    {
        selected.insert(entry.first);
        planes.insert(planes.end(),entry.second.begin(),entry.second.end());
    }
    std::sort(planes.begin(),planes.end());
    planes.erase(std::unique(planes.begin(),planes.end()),planes.end());
    auto alive=[&](label c){return c>=0&&c<raw.nCells&&raw.cellAlive[c];};
    std::set<label> context=selected,physical;
    for(label c:selected)for(label f:cf[c])
    {
        physical.insert(f);
        for(label n:{raw.owner[f],raw.neighbour[f]})if(alive(n))context.insert(n);
    }
    if(finiteCut)
    {
        context.insert(halo.parents.begin(),halo.parents.end());
        if(context.size()>Finite::haloParentCap)return fail("finite-halo-parent-cap");
    }
    SparseMesh draft;std::map<label,label> cellToLocal,pointToLocal,faceToLocal;
    std::vector<label> rootOf,pointNative,faceSource;
    for(label c:context)
    {
        if(!work())return fail("clock");
        if(finiteCut&&(!alive(c)||raw.cellRank[c]!=rank||cf[c].size()<4||cf[c].size()>64))
            return fail("finite-context-parent");
        cellToLocal.emplace(c,draft.nCells++);rootOf.push_back(c);
        draft.cellAlive.push_back(1);draft.cellRank.push_back(raw.cellRank[c]);
    }
    std::set<label> contextFaces;
    for(label c:context)contextFaces.insert(cf[c].begin(),cf[c].end());
    if(finiteCut&&contextFaces.size()>Finite::surfaceFaceCap)return fail("finite-context-face-cap");
    for(label f:contextFaces)
    {
        if(!work()||f<0||std::size_t(f)>=raw.faces.size()||!raw.faceAlive[f])
            return fail("compound-context");
        std::vector<label> ring;
        for(label p:raw.faces[f])
        {
            auto found=pointToLocal.find(p);
            if(found==pointToLocal.end())
            {
                if(p<0||std::size_t(p)>=raw.points.size())return fail("compound-point");
                const label local=draft.points.size();draft.points.push_back(raw.points[p]);
                pointNative.push_back(p);found=pointToLocal.emplace(p,local).first;
                if(finiteCut&&pointNative.size()>Finite::pointCap)return fail("finite-point-cap");
            }
            ring.push_back(found->second);
        }
        const auto o=cellToLocal.find(raw.owner[f]),n=cellToLocal.find(raw.neighbour[f]);
        if(o==cellToLocal.end())
        {
            if(n==cellToLocal.end())return fail("compound-context-endpoint");
            // Such a context-only face has a live owner outside this sparse
            // copy. It cannot be changed; reversing here keeps its real local
            // neighbour shell and is restored to native orientation below.
            std::reverse(ring.begin(),ring.end());
        }
        faceToLocal.emplace(f,draft.faces.size());draft.faces.push_back(std::move(ring));
        draft.owner.push_back(o==cellToLocal.end()?n->second:o->second);
        draft.neighbour.push_back(o!=cellToLocal.end()&&n!=cellToLocal.end()?n->second:-1);
        draft.patch.push_back(raw.patch[f]);draft.zone.push_back(raw.zone[f]);
        draft.faceAlive.push_back(1);faceSource.push_back(f);
    }
    std::vector<S::Birth> births;std::vector<Finite::Plane> birthPlanes;
    std::set<label> changedSurfaceSources;
    auto rebuildCf=[&]()
    {
        std::vector<std::vector<label>> result(draft.nCells);
        for(label f=0;f<label(draft.faces.size());++f)if(draft.faceAlive[f])
        {
            result[draft.owner[f]].push_back(f);
            if(draft.neighbour[f]>=0)result[draft.neighbour[f]].push_back(f);
        }
        return result;
    };
    if(hasSurface)
    {
        for(std::size_t j=0;j<surfacePlan.births.size();++j)
        {
            const auto& birth=surfacePlan.births[j];
            if(!work()||!pointToLocal.count(birth.first)||!pointToLocal.count(birth.second))
                return fail("surface-edge-context-birth");
            const label native=raw.points.size()+j,local=draft.points.size();
            if(!pointToLocal.emplace(native,local).second)return fail("surface-edge-native-point");
            pointNative.push_back(native);draft.points.push_back(birth.position);
            births.push_back(birth);birthPlanes.push_back(surfacePlan.birthPlanes[j]);
        }
        for(const auto& row:surfacePlan.rows)
        {
            if(!work()||!faceToLocal.count(row.source))return fail("surface-edge-context-source");
            const label local=faceToLocal.at(row.source);std::vector<label> ring;
            for(label p:row.ring)
            {
                const auto found=pointToLocal.find(p);
                if(found==pointToLocal.end())return fail("surface-edge-context-point");
                ring.push_back(found->second);
            }
            draft.faces[local]=std::move(ring);
            changedSurfaceSources.insert(row.source);physical.insert(row.source);
        }
    }
    auto localCf=rebuildCf();std::set<label> changedRoots;DraftPointIndex draftIndex;
    for(const auto& plane:planes)
    {
        Finite::Request volumeScope;volumeScope.mode=cutScope;
        if(finiteCut)for(label c=0;c<draft.nCells;++c)
        {
            if(!work())return fail("clock");
            const auto roots=assigned.find(rootOf[c]);
            if(roots==assigned.end()||!std::binary_search(roots->second.begin(),roots->second.end(),plane))continue;
            bool pos=false,neg=false;
            for(label f:localCf[c])for(label p:draft.faces[f])
            {pos=pos||draft.points[p][plane.first]>plane.second;neg=neg||draft.points[p][plane.first]<plane.second;}
            if(pos&&neg)volumeScope.volumeParents.insert(c);
        }
        for(label seed=0;seed<draft.nCells;++seed)
        {
            if(!work())return fail("clock");
            const auto rootPlanes=assigned.find(rootOf[seed]);
            if(rootPlanes==assigned.end()||!std::binary_search(rootPlanes->second.begin(),
                rootPlanes->second.end(),plane))continue;
            bool pos=false,neg=false;
            for(label f:localCf[seed])for(label p:draft.faces[f])
            {pos=pos||draft.points[p][plane.first]>plane.second;neg=neg||draft.points[p][plane.first]<plane.second;}
            if(!pos||!neg)continue;
            if(finiteCut&&!volumeScope.volumeParents.count(seed))return fail("finite-incomplete-volume-cut");
            if(!draftIndex.ensure(draft,available))return fail("compound-point-index");
            S::Plan cut;std::string refusal;
            if(!S::prepare(draft,localCf,seed,plane.first,plane.second,rank,draftIndex.get(),
                [&](label f){return faceSource[f]>=0&&blockedSource&&blockedSource(faceSource[f]);},
                available,cut,&refusal,nullptr,
                [&](label c)
                {
                    const auto real=assigned.find(rootOf[c]);
                    return real!=assigned.end()&&std::binary_search(real->second.begin(),real->second.end(),plane);
                },{},preserveConvexSourceRings,volumeScope,
                [&](label f){return faceSource[f]>=0&&actualCoupled&&actualCoupled(faceSource[f]);}))
            {if(why)*why=refusal;return false;}
            if(finiteCut)
            {
                if(cut.birthPlanes.size()!=cut.births.size())return fail("finite-birth-plane-count");
                for(const auto& entry:cut.surfaceRefinements)
                {
                    const label source=faceSource.at(entry.first);
                    if(source<0)return fail("finite-halo-generated-source");
                    changedSurfaceSources.insert(source);physical.insert(source);
                }
            }
            for(const auto& b:cut.births)
            {
                if(!work())return fail("clock");
                S::GridKey key;if(!S::gridKey(b.position,key))return fail("birth-point");
                for(label x=-1;x<=1;++x)for(label y=-1;y<=1;++y)for(label z=-1;z<=1;++z)
                {
                    const auto found=rawIndex.grid.find(S::GridKey{{key[0]+x,key[1]+y,key[2]+z}});
                    if(found==rawIndex.grid.end())continue;
                    for(label p:found->second)
                        if(magSqr(b.position-raw.points[p])<=9e-12)return fail("active-point-3um");
                }
                if(std::size_t(b.first)>=pointNative.size()||std::size_t(b.second)>=pointNative.size())
                    return fail("compound-birth-order");
                S::Birth inherited;inherited.first=pointNative[b.first];inherited.second=pointNative[b.second];
                inherited.position=b.position;
                if(births.size()>=std::size_t(std::numeric_limits<label>::max())-raw.points.size())
                    return fail("point-label");
                pointNative.push_back(raw.points.size()+births.size());births.push_back(inherited);
                if(finiteCut)birthPlanes.push_back(plane);
                draft.points.push_back(b.position);
                if(finiteCut&&draft.points.size()>Finite::pointCap)return fail("finite-point-cap");
            }
            for(const auto& c:cut.cells)
            {
                if(c.child!=draft.nCells)return fail("compound-child-order");
                rootOf.push_back(rootOf[c.parent]);draft.cellAlive.push_back(1);
                draft.cellRank.push_back(rank);++draft.nCells;changedRoots.insert(rootOf[c.parent]);
            }
            // The next cut's request is rebuilt from the remaining crossed draft
            // children; no halo parent is promoted to a volume root.
            if(finiteCut)volumeScope.volumeParents.clear();
            // Newly appended cell slots start empty; existing incidence order holds.
            localCf.resize(draft.nCells);
            for(const auto& row:cut.rows)
            {
                const bool existed=row.native<label(draft.faces.size());
                const label beforeOwner=existed?draft.owner[row.native]:-1;
                const label beforeNeighbour=existed?draft.neighbour[row.native]:-1;
                const bool beforeAlive=existed&&draft.faceAlive[row.native];
                const bool beforeActive=existed&&DraftPointIndex::active(draft,row.native);
                const std::vector<label> beforeRing=existed?draft.faces[row.native]:std::vector<label>();
                const label source=row.source>=0?faceSource.at(row.source):-1;
                if(row.native<label(draft.faces.size()))
                {
                    draft.faces[row.native]=row.ring;draft.owner[row.native]=row.owner;
                    draft.neighbour[row.native]=row.neighbour;draft.patch[row.native]=row.patch;
                    draft.zone[row.native]=row.zone;faceSource[row.native]=source;
                }
                else
                {
                    if(row.native!=label(draft.faces.size()))return fail("compound-face-order");
                    if(finiteCut&&draft.faces.size()>=Finite::surfaceFaceCap)return fail("finite-context-face-cap");
                    draft.faces.push_back(row.ring);draft.owner.push_back(row.owner);
                    draft.neighbour.push_back(row.neighbour);draft.patch.push_back(row.patch);
                    draft.zone.push_back(row.zone);draft.faceAlive.push_back(1);faceSource.push_back(source);
                }
                updateCellFaces(localCf,row.native,beforeOwner,beforeNeighbour,beforeAlive,
                    draft.owner[row.native],draft.neighbour[row.native],draft.faceAlive[row.native]);
                if(!draftIndex.replaceFace(draft,row.native,beforeRing,beforeActive,available))
                    return fail("compound-point-index");
            }
        }
    }
    if(changedRoots!=selected)return fail("compound-missing-cut");
    if(planes.empty()&&!hasSurface)return fail("compound-empty");
    S::Plan plan;plan.axis=planes.empty()?surfacePlan.axis:planes.front().first;
    plan.coordinate=planes.empty()?surfacePlan.coordinate:planes.front().second;
    if(hasSurface)
    {
        plan.surfaceEdgeBirths=*surfaceBirthRequests;
        for(label c:halo.parents)if(!selected.count(c))plan.surfaceWholeParents.insert(c);
    }
    plan.births=std::move(births);
    if(finiteCut)
    {
        plan.cutScope=cutScope;plan.volumeRoots.assign(selected.begin(),selected.end());
        plan.birthPlanes=std::move(birthPlanes);
    }
    std::map<label,label> partNative;label nextCell=raw.nCells;
    for(label root:selected)
    {
        S::Cell cell;cell.parent=root;
        S::Part retained;retained.cell=root;cell.parts.push_back(retained);
        const label original=cellToLocal.at(root);partNative.emplace(original,root);
        for(label c=label(context.size());c<draft.nCells;++c)if(rootOf[c]==root)
        {
            if(nextCell==std::numeric_limits<label>::max())return fail("cell-label");
            S::Part part;part.cell=nextCell++;cell.parts.push_back(part);partNative.emplace(c,part.cell);
        }
        if(cell.parts.size()<2)return fail("compound-missing-child");
        cell.child=cell.parts[1].cell;plan.cells.push_back(std::move(cell));
    }
    label nextFace=raw.faces.size();std::set<label> reused;
    for(label f=0;f<label(draft.faces.size());++f)
    {
        if(!work())return fail("clock");
        if(!draft.faceAlive[f]||(!selected.count(rootOf[draft.owner[f]])
            &&!(draft.neighbour[f]>=0&&selected.count(rootOf[draft.neighbour[f]]))
            &&!(finiteCut&&changedSurfaceSources.count(faceSource[f]))))continue;
        if(nextFace==std::numeric_limits<label>::max())return fail("face-label");
        S::Row row;row.source=faceSource[f];
        if(row.source>=0)
        {
            if(!physical.count(row.source))return fail("compound-source-scope");
            row.native=reused.insert(row.source).second?row.source:nextFace++;
            row.patch=raw.patch[row.source];row.zone=raw.zone[row.source];
        }
        else {row.native=nextFace++;row.patch=-1;row.zone=-1;}
        const auto o=partNative.find(draft.owner[f]),n=partNative.find(draft.neighbour[f]);
        row.owner=o==partNative.end()?rootOf[draft.owner[f]]:o->second;
        row.neighbour=draft.neighbour[f]<0?-1:(n==partNative.end()?rootOf[draft.neighbour[f]]:n->second);
        for(label p:draft.faces[f])row.ring.push_back(pointNative.at(p));
        plan.rows.push_back(std::move(row));
    }
    if(reused!=physical)return fail("compound-source-coverage");
    if(finiteCut)
    {
        for(label source:changedSurfaceSources)
        {
            if(selected.count(raw.owner[source])||selected.count(raw.neighbour[source]))continue;
            if(actualCoupled(source))
            {
                bool matched=false;
                if(hasSurface)for(const auto& request:*surfaceBirthRequests)
                    matched=matched||request.source==source;
                if(!matched)return fail("finite-halo-native-role");
            }
            // Retain the existing final row order and native append mapping.
            // Grouping below records actual pieces; it never reassigns their IDs.
            std::map<label,std::vector<label>> actualPieces;
            std::vector<std::vector<label>> rings;
            for(const auto& row:plan.rows)if(row.source==source)
            {
                if(!work()||row.owner!=raw.owner[source]||row.neighbour!=raw.neighbour[source]
                    ||row.patch!=raw.patch[source]||row.zone!=raw.zone[source]
                    ||row.native<0||(row.native!=source&&std::size_t(row.native)<raw.faces.size())
                    ||row.ring.size()<3||row.ring.size()>Finite::ringCap
                    ||!actualPieces.emplace(row.native,row.ring).second)
                    return fail("finite-halo-piece-binding");
                rings.push_back(row.ring);
                if(rings.size()>Finite::ringCap)return fail("finite-halo-piece-cap");
            }
            if(actualPieces.empty()||!actualPieces.count(source))return fail("finite-halo-native-role");
            std::vector<label> outer;
            if(rings.size()==1)outer=rings.front();
            else
            {
                dcCartExactBinary64Geometry::Context exact(available);
                if(!dcCartSourcePlanarConeGroups::Detail::outline(rings,outer,exact)||!exact.good())
                    return fail("finite-halo-piece-outline");
            }
            const auto at=[&](label p)->const point&{return S::coordinate(raw,plan,p);};
            auto proof=Finite::SurfaceRefinementProducer::capture(raw,source,outer,actualPieces,at,available);
            if(!proof||!plan.surfaceRefinements.emplace(source,std::move(proof)).second)
                return fail("finite-halo-proof");
        }
    }
    // Preserve the original plan.rows order at each actual endpoint once.
    // No geometry or quality result is cached by this topology-only index.
    std::map<label,std::vector<std::size_t>> rowsByEndpoint;
    for(std::size_t i=0;i<plan.rows.size();++i)
    {
        const auto& row=plan.rows[i];
        rowsByEndpoint[row.owner].push_back(i);
        if(row.neighbour>=0&&row.neighbour!=row.owner)
            rowsByEndpoint[row.neighbour].push_back(i);
    }
    for(auto& cell:plan.cells)
    {
        point reference=point::zero;std::size_t count=0;
        std::vector<S::Face> original;
        for(label f:cf[cell.parent])
        {
            original.push_back({f,raw.faces[f],raw.owner[f]==cell.parent});
            for(label p:raw.faces[f]){reference+=raw.points[p];++count;}
        }
        if(!count)return fail("compound-source-shell");reference/=scalar(count);
        if(!S::geometry(raw,plan,original,reference,available,cell.before))return fail("old-geometry");
        scalar volume=0;std::array<long double,4> physicalMoment{{0,0,0,0}};
        for(auto& part:cell.parts)
        {
            const auto rows=rowsByEndpoint.find(part.cell);
            if(rows!=rowsByEndpoint.end())for(std::size_t i:rows->second)
            {
                const auto& row=plan.rows[i];
                part.shell.push_back({row.native,row.ring,row.owner==part.cell});
            }
            if(!S::geometry(raw,plan,part.shell,reference,available,part.geometry))
                return fail("part-geometry");
            volume+=part.geometry.volume;
            for(label k=0;k<4;++k)physicalMoment[k]+=part.geometry.physical[k];
        }
        if(mag(volume-cell.before.volume)/max(cell.before.volume,VSMALL)>1e-10)return fail("volume");
        const scalar span=max(mag(cell.before.hi-cell.before.lo),scalar(1e-6));
        for(label k=0;k<4;++k)
            if(std::abs(physicalMoment[k]-cell.before.physical[k])
                /std::max(cell.before.physical[0]*(k?span:1),static_cast<long double>(VSMALL))>1e-10L)
                return fail("physical-moment");
        cell.a=cell.parts[0].geometry;cell.b=cell.parts[1].geometry;
    }
    if(!work())return fail("clock");
    if(completedPlanes)*completedPlanes=planes;
    if(completedRoots)*completedRoots=assigned;
    if(completedHalo)*completedHalo=std::move(halo);
    plan.ready=true;out=std::move(plan);return true;
}
} }
#endif
