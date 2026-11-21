#ifndef dcCartRawUnionStockMetrics_H
#define dcCartRawUnionStockMetrics_H

#include "point.H"
#include "vector.H"
#include "symmTensor.H"
#include "mathematicalConstants.H"
#include "dcCartRetainedPlanarBaseStockTets.H"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <functional>
#include <map>
#include <set>
#include <utility>
#include <vector>

namespace Foam
{
namespace dcCartRawUnionStockMetrics
{

// Pure geometry/stock-role measurements for a caller-owned staged raw shell.
// Native face/cell/point IDs remain literal. Nothing here grants material,
// source, patch, zone, lineage, coupled, publisher, or mutation authority.
// Formula reference: currentC SHA256
// 3dfc65ec2a53a2c5fcc8f170ec21b4106ebb48fb34b37218032405eccedf6916,
// evaluateUnionCell 18671-18742, faceUnionMetrics 18800-18846,
// unionAspect 18929-18934, inherited roles 19128-19196 / 19217-19221.
using FaceOF = std::function<void
    (const std::vector<label>&, vector&, point&)>;
using CoupledTypePatch = std::function<bool(label)>;
using Available = std::function<bool()>;

struct Face
{
    label id=-1, owner=-1, neighbour=-1, patch=-1, zone=-1;
    std::vector<label> ring; // Actual owner-oriented native point-ID ring.

    Face() = default;
    Face(label f,label o,label n,label p,label z,const std::vector<label>& r)
    : id(f),owner(o),neighbour(n),patch(p),zone(z),ring(r) {}
};

// Optional raw-stage proof only; never a final written-cell C certificate.
using PartitionOnly = std::function<bool(const Face&,const Face&)>;

// Existing first face-centre predicate operands only. Keys belong to the
// measured future shell; an artificial key is not a published native face.
struct BlockingFacePair
{
    label face=-1,otherFace=-1,owner=-1,neighbour=-1,otherOwner=-1,otherNeighbour=-1;
    scalar cosine=0;
};
struct Cell
{
    label id=-1;
    point centre=point::zero;
    scalar volume=0, determinant=0, aspect=GREAT;
    // With PartitionOnly this is a raw-stage directed/convex shell result,
    // not an official final written-cell concavity acceptance.
    bool finite=false, strictGeometry=false;
    // IDs of the first existing convexity predicate failure; no new geometry test.
    label blockingFace=-1,blockingPoint=-1;
    BlockingFacePair blockingPair;
    // Same order as the fullShell argument, outward from this actual cell.
    std::vector<label> faceIds;
    std::vector<vector> areas;
    std::vector<point> centres;
    const char* reason="unmeasured";
};

struct FacetQuality
{
    // Literal currentC order: [D placeholder, W, R, S, N]. D is a cell role.
    std::array<scalar,5> v{{GREAT,GREAT,GREAT,0,0}};
    bool numeric=false, valid=false;
    label commonAnchor=-1;
    const char* reason="unmeasured";
};

inline bool workAvailable(const Available& available)
{ return !available || available(); }

inline bool finitePoint(const vector& p)
{
    return std::isfinite(p.x()) && std::isfinite(p.y())
        && std::isfinite(p.z());
}

inline bool validRingIds
(
    const Face& face,
    const std::vector<point>& points,
    const Available& available
)
{
    if (face.id<0 || face.owner<0 || face.neighbour<-1
        || face.owner==face.neighbour || face.patch<-1 || face.zone<-1
        || face.ring.size()<3) return false;
    for (label p:face.ring)
        if (!workAvailable(available) || p<0
            || std::size_t(p)>=points.size()) return false;
    return true;
}

// Literal writer Newell-area gate. OF Sf/face centres are supplied separately;
// this area is never substituted for them or for the actual cell centre.
inline vector writerArea
(
    const std::vector<label>& ring,
    const std::vector<point>& points
)
{
    vector n(vector::zero);
    for (std::size_t j=0;j<ring.size();++j)
    {
        const point& a=points[ring[j]];
        const point& b=points[ring[(j+1)%ring.size()]];
        n.x()+=(a.y()-b.y())*(a.z()+b.z());
        n.y()+=(a.z()-b.z())*(a.x()+b.x());
        n.z()+=(a.x()-b.x())*(a.y()+b.y());
    }
    n*=0.5;
    return n;
}

inline scalar aspect(const Cell& geometry)
{
    vector sum(Zero);
    for (const vector& a:geometry.areas) sum+=cmptMag(a);
    return max(cmptMax(sum)/(cmptMin(sum)+ROOTVSMALL),
        cmptSum(sum)/(6*std::pow(max(ROOTVSMALL,geometry.volume),scalar(2)/3)));
}

// fullShell contains each actual surviving incident facet once, in the caller's
// native order, including unchanged outside faces. faceOF must invoke the actual
// OF faceAreaCentre path over this exact ring and these exact native points.
// coupledTypePatch consumes a PATCH ID, matching currentC's patch type lookup.
// strict=false measures the current endpoint CC/V/D; strict=true additionally
// verifies the complete directed/convex shell. repaired adds the literal 3um
// face-pyramid distance requirement. D/AR role admission remains explicit below.
inline Cell measure
(
    label c,
    const std::vector<Face>& fullShell,
    const std::vector<point>& points,
    const FaceOF& faceOF,
    const CoupledTypePatch& coupledTypePatch,
    const Available& available,
    bool strict,
    bool repaired=true,
    const PartitionOnly& partitionOnly=PartitionOnly()
)
{
    Cell out;
    out.id=c;
    auto refuse=[&](const char* why)->Cell { out.reason=why;return out; };
    if (!workAvailable(available)) return refuse("clock");
    if (c<0 || !faceOF || fullShell.size()<4) return refuse("cell-shell");
    point estimate(point::zero);
    scalar totalArea=0,selectedArea=0;
    label selectedCount=0;
    std::vector<label> vertices;
    std::map<std::pair<label,label>,std::pair<label,label>> edges;
    std::set<label> seenFaces;
    std::vector<unsigned char> selected;
    selected.reserve(fullShell.size());
    for (const Face& row:fullShell)
    {
        if (!workAvailable(available)) return refuse("clock");
        if (!validRingIds(row,points,available)
            || (row.owner!=c && row.neighbour!=c)
            || !seenFaces.insert(row.id).second) return refuse("native-incidence");
        vector sf(vector::zero);
        point fc(point::zero);
        faceOF(row.ring,sf,fc);
        if (!workAvailable(available)) return refuse("clock");
        const vector a=(row.owner==c ? 1 : -1)*sf;
        if (!finitePoint(a) || !finitePoint(fc) || mag(a)<=1e-12)
            return refuse("face-of-geometry");
        out.faceIds.push_back(row.id);
        out.areas.push_back(a);
        out.centres.push_back(fc);
        estimate+=fc;
        totalArea+=mag(a);
        const bool use=row.patch<0
            || (coupledTypePatch && coupledTypePatch(row.patch));
        selected.push_back(use ? 1 : 0);
        if (use) { selectedArea+=mag(a);++selectedCount; }
        if (!strict) continue;
        const vector wa=writerArea(row.ring,points);
        if (!finitePoint(wa) || mag(wa)<=1e-12) return refuse("writer-area");
        for (std::size_t j=0;j<row.ring.size();++j)
        {
            if (!workAvailable(available)) return refuse("clock");
            label a=row.ring[j],b=row.ring[(j+1)%row.ring.size()];
            if (row.owner!=c) std::swap(a,b);
            auto& e=edges[std::minmax(a,b)];
            ++e.first;
            e.second+=a<b ? 1 : -1;
            vertices.push_back(a);
        }
    }
    estimate/=fullShell.size();
    scalar pyramid3Sum=0;
    point weighted(point::zero);
    for (std::size_t j=0;j<fullShell.size();++j)
    {
        if (!workAvailable(available)) return refuse("clock");
        const scalar p=out.areas[j]&(out.centres[j]-estimate);
        pyramid3Sum+=p;
        weighted+=p*(.75*out.centres[j]+.25*estimate);
    }
    if (!std::isfinite(pyramid3Sum) || pyramid3Sum<=VSMALL
        || !finitePoint(weighted)) return refuse("cell-pyramid-volume");
    out.volume=pyramid3Sum/3;
    out.centre=weighted/pyramid3Sum;
    if (!finitePoint(out.centre) || !selectedCount || selectedArea<=VSMALL)
        return refuse("cell-centre-selected-area");
    const scalar mean=selectedArea/selectedCount;
    symmTensor tensor(Zero);
    for (std::size_t j=0;j<fullShell.size();++j)
    {
        if (!workAvailable(available)) return refuse("clock");
        if (selected[j]) tensor+=sqr(out.areas[j]/mean);
    }
    out.determinant=mag(det(tensor))/8;
    if (!std::isfinite(out.determinant)) return refuse("determinant");
    if (strict)
    {
        vector closure(vector::zero);
        point lo(GREAT,GREAT,GREAT),hi(-GREAT,-GREAT,-GREAT);
        std::sort(vertices.begin(),vertices.end());
        vertices.erase(std::unique(vertices.begin(),vertices.end()),vertices.end());
        for (label p:vertices)
        {
            if (!workAvailable(available)) return refuse("clock");
            if (!finitePoint(points[p])) return refuse("point-finite");
            lo=min(lo,points[p]);
            hi=max(hi,points[p]);
        }
        const scalar span=max(mag(hi-lo),scalar(1e-6));
        for (const auto& e:edges)
        {
            if (!workAvailable(available)) return refuse("clock");
            if (e.second.first!=2 || e.second.second!=0)
                return refuse("directed-edge-closure");
        }
        for (std::size_t j=0;j<fullShell.size();++j)
        {
            if (!workAvailable(available)) return refuse("clock");
            const vector a=out.areas[j],normal=a/mag(a);
            const point fc=out.centres[j];
            closure+=a;
            const scalar pv=(a&(fc-out.centre))/3;
            if (!std::isfinite(pv) || pv<=1e-15*out.volume)
                return refuse("positive-pyramid");
            if (repaired && (a&(fc-out.centre))/mag(a)<=3e-6)
                return refuse("repaired-distance-3um");
            for (label p:fullShell[j].ring)
            {
                if (!workAvailable(available)) return refuse("clock");
                if (mag((points[p]-fc)&normal)>1e-10*span)
                    return refuse("face-planarity");
            }
            for (label p:vertices)
            {
                if (!workAvailable(available)) return refuse("clock");
                if (((points[p]-fc)&normal)>1e-10*span)
                {
                    out.blockingFace=fullShell[j].id;out.blockingPoint=p;
                    return refuse("cell-convexity");
                }
            }
            // Default is the literal official face-centre concavity gate. A raw-stage
            // caller may prove an exact same-source coplanar partition; all
            // non-partition pairs still pass the unchanged official gate.
            for (std::size_t k=0;k<fullShell.size();++k) if (j!=k)
            {
                if (!workAvailable(available)) return refuse("clock");
                vector d=out.centres[k]-fc;
                d/=max(mag(d),VSMALL);
                const scalar cosine=normal&d;
                const auto recordPair=[&]()
                {
                    out.blockingPair={fullShell[j].id,fullShell[k].id,
                        fullShell[j].owner,fullShell[j].neighbour,
                        fullShell[k].owner,fullShell[k].neighbour,cosine};
                };
                if (!std::isfinite(cosine))
                {recordPair();return refuse("face-centre-concavity");}
                if (cosine>-1e-6
                    && (!partitionOnly || !partitionOnly(fullShell[j],fullShell[k])))
                {recordPair();return refuse("face-centre-concavity");}
            }
        }
        if (mag(closure)/max(totalArea,VSMALL)>1e-12)
            return refuse("area-closure");
        out.strictGeometry=true;
    }
    out.aspect=aspect(out);
    if (!workAvailable(available)) return refuse("clock");
    out.finite=true;
    out.reason=nullptr;
    return out;
}

// Measurement-only carrier populated from the same actual installed SDK
// polyMesh: cellCentres()/cellVolumes(), official cellDeterminant with
// geometricD()/syncTools selection, and official cellClosedness aspect.
// known=false is an unknown measurement and always refuses. Neither this
// public value package nor measureInstalledSDK grants Source/peer/publication.
struct InstalledSDKCell
{
    bool known=false;
    point centre=point::zero;
    scalar volume=0,determinant=0,aspect=GREAT;
};

// Full owner-directed installed CF and actual SDK Sf/FC are supplied by the
// SDK-only faceOF callback. All native ID/ring/closure/planarity/convexity,
// positive-pyramid, original repaired3um and strictC gates remain mandatory.
// Actual SDK CC/V/D/AR enter before every geometry test, never after it.
inline Cell measureInstalledSDK
(
    label c,
    const std::vector<Face>& fullShell,
    const std::vector<point>& points,
    const FaceOF& faceOF,
    const CoupledTypePatch& coupledTypePatch,
    const Available& available,
    const InstalledSDKCell& installed,
    bool strict,
    bool repaired=true
)
{
    Cell out;
    out.id=c;
    out.centre=installed.centre;
    out.volume=installed.volume;
    out.determinant=installed.determinant;
    out.aspect=installed.aspect;
    auto refuse=[&](const char* why)->Cell { out.reason=why;return out; };
    if (!workAvailable(available)) return refuse("clock");
    if (c<0 || !faceOF || !coupledTypePatch || fullShell.size()<4)
        return refuse("installed-SDK-cell-shell");
    if (!installed.known || !finitePoint(out.centre)
        || !std::isfinite(out.volume) || out.volume<=VSMALL
        || !std::isfinite(out.determinant) || out.determinant<0
        || !std::isfinite(out.aspect) || out.aspect<=0)
        return refuse("installed-SDK-cell-values-unknown");
    scalar totalArea=0,selectedArea=0;
    label selectedCount=0;
    std::vector<label> vertices;
    std::map<std::pair<label,label>,std::pair<label,label>> edges;
    std::set<label> seenFaces;
    std::vector<unsigned char> selected;
    selected.reserve(fullShell.size());
    for (const Face& row:fullShell)
    {
        if (!workAvailable(available)) return refuse("clock");
        if (!validRingIds(row,points,available)
            || (row.owner!=c && row.neighbour!=c)
            || !seenFaces.insert(row.id).second) return refuse("native-incidence");
        vector sf(vector::zero);
        point fc(point::zero);
        faceOF(row.ring,sf,fc);
        if (!workAvailable(available)) return refuse("clock");
        const vector a=(row.owner==c ? 1 : -1)*sf;
        if (!finitePoint(a) || !finitePoint(fc) || mag(a)<=1e-12)
            return refuse("face-of-geometry");
        out.faceIds.push_back(row.id);
        out.areas.push_back(a);
        out.centres.push_back(fc);
        totalArea+=mag(a);
        const bool use=row.patch<0
            || (coupledTypePatch && coupledTypePatch(row.patch));
        selected.push_back(use ? 1 : 0);
        if (use) { selectedArea+=mag(a);++selectedCount; }
        if (!strict) continue;
        const vector wa=writerArea(row.ring,points);
        if (!finitePoint(wa) || mag(wa)<=1e-12) return refuse("writer-area");
        for (std::size_t j=0;j<row.ring.size();++j)
        {
            if (!workAvailable(available)) return refuse("clock");
            label a=row.ring[j],b=row.ring[(j+1)%row.ring.size()];
            if (row.owner!=c) std::swap(a,b);
            auto& e=edges[std::minmax(a,b)];
            ++e.first;
            e.second+=a<b ? 1 : -1;
            vertices.push_back(a);
        }
    }
    // CC/V/D/AR above are owned actual installed SDK values.
    // Never replace them with native face-loop arithmetic before strict gates.
    if (strict)
    {
        vector closure(vector::zero);
        point lo(GREAT,GREAT,GREAT),hi(-GREAT,-GREAT,-GREAT);
        std::sort(vertices.begin(),vertices.end());
        vertices.erase(std::unique(vertices.begin(),vertices.end()),vertices.end());
        for (label p:vertices)
        {
            if (!workAvailable(available)) return refuse("clock");
            if (!finitePoint(points[p])) return refuse("point-finite");
            lo=min(lo,points[p]);
            hi=max(hi,points[p]);
        }
        const scalar span=max(mag(hi-lo),scalar(1e-6));
        for (const auto& e:edges)
        {
            if (!workAvailable(available)) return refuse("clock");
            if (e.second.first!=2 || e.second.second!=0)
                return refuse("directed-edge-closure");
        }
        for (std::size_t j=0;j<fullShell.size();++j)
        {
            if (!workAvailable(available)) return refuse("clock");
            const vector a=out.areas[j],normal=a/mag(a);
            const point fc=out.centres[j];
            closure+=a;
            const scalar pv=(a&(fc-out.centre))/3;
            if (!std::isfinite(pv) || pv<=1e-15*out.volume)
                return refuse("positive-pyramid");
            if (repaired && (a&(fc-out.centre))/mag(a)<=3e-6)
                return refuse("repaired-distance-3um");
            for (label p:fullShell[j].ring)
            {
                if (!workAvailable(available)) return refuse("clock");
                if (mag((points[p]-fc)&normal)>1e-10*span)
                    return refuse("face-planarity");
            }
            for (label p:vertices)
            {
                if (!workAvailable(available)) return refuse("clock");
                if (((points[p]-fc)&normal)>1e-10*span)
                {
                    out.blockingFace=fullShell[j].id;out.blockingPoint=p;
                    return refuse("cell-convexity");
                }
            }
            // Installed cells keep the literal strict face-centre concavity gate.
            // A raw partition exception cannot authorize an installed SDK shell.
            for (std::size_t k=0;k<fullShell.size();++k) if (j!=k)
            {
                if (!workAvailable(available)) return refuse("clock");
                vector d=out.centres[k]-fc;
                d/=max(mag(d),VSMALL);
                const scalar cosine=normal&d;
                const auto recordPair=[&]()
                {
                    out.blockingPair={fullShell[j].id,fullShell[k].id,
                        fullShell[j].owner,fullShell[j].neighbour,
                        fullShell[k].owner,fullShell[k].neighbour,cosine};
                };
                if (!std::isfinite(cosine))
                {recordPair();return refuse("face-centre-concavity");}
                if (cosine>-1e-6)
                {recordPair();return refuse("face-centre-concavity");}
            }
        }
        if (mag(closure)/max(totalArea,VSMALL)>1e-12)
            return refuse("area-closure");
        out.strictGeometry=true;
    }
    if (!workAvailable(available)) return refuse("clock");
    out.finite=true;
    out.reason=nullptr;
    return out;
}

// Numeric screen over a real base triangle and caller's current OF centre.
// This predictor is not a certificate for later selected normal bases/apex.
struct TriangularConeQuality
{
    scalar determinant=0,aspect=GREAT;
    bool numeric=false,available=true;
};
inline TriangularConeQuality triangularConeQuality
(
    const point& a,const point& b,const point& d,const point& apex,
    bool selectedBase,const Available& available
)
{
    TriangularConeQuality out;
    if (!workAvailable(available)) {out.available=false;return out;}
    if (!finitePoint(a)||!finitePoint(b)||!finitePoint(d)||!finitePoint(apex))
        return out;
    const std::array<vector,4> sf{{
        ((b-a)^(d-a))*.5,
        ((a-b)^(apex-b))*.5,
        ((b-d)^(apex-d))*.5,
        ((d-a)^(apex-a))*.5}};
    const std::array<point,4> fc{{
        (a+b+d)/3,(b+a+apex)/3,(d+b+apex)/3,(a+d+apex)/3}};
    point estimate(point::zero);scalar selectedArea=0;
    vector sum(Zero);label selectedCount=0;
    for (std::size_t j=0;j<4;++j)
    {
        if (!workAvailable(available)) {out.available=false;return out;}
        if (!finitePoint(sf[j])||!finitePoint(fc[j])||mag(sf[j])<=1e-12)
            return out;
        estimate+=fc[j];sum+=cmptMag(sf[j]);
        if (j||selectedBase){selectedArea+=mag(sf[j]);++selectedCount;}
    }
    estimate/=4;scalar volume3=0;
    for (std::size_t j=0;j<4;++j)volume3+=sf[j]&(fc[j]-estimate);
    if (!std::isfinite(volume3)||volume3<=VSMALL||selectedArea<=VSMALL)
        return out;
    const scalar mean=selectedArea/selectedCount;
    symmTensor tensor(Zero);
    for (std::size_t j=0;j<4;++j)if (j||selectedBase)tensor+=sqr(sf[j]/mean);
    const scalar determinant=mag(det(tensor))/8,volume=volume3/3;
    const scalar ar=max(cmptMax(sum)/(cmptMin(sum)+ROOTVSMALL),
        cmptSum(sum)/(6*std::pow(max(ROOTVSMALL,volume),scalar(2)/3)));
    out.determinant=determinant;out.aspect=ar;
    out.numeric=std::isfinite(determinant)&&std::isfinite(ar);
    return out;
}

inline bool triangularConeDebt
(
    const point& a,const point& b,const point& d,const point& apex,
    bool selectedBase,const Available& available
)
{
    const auto q=triangularConeQuality(a,b,d,apex,selectedBase,available);
    return q.available&&(!q.numeric||q.determinant<.001||q.aspect>1000);
}

// Whole predictor-recipe aggregate. Unknown numeric cones remain explicit debt;
// they cannot appear in an accepted after recipe. These counts are work/repair
// selection quantities, not written-mesh quality certificates.
struct ConeDebt
{
    std::size_t cones=0,badD=0,badAspect=0,unknown=0;
    scalar minD=GREAT,maxAspect=0;
    bool complete=false;
    bool bad() const {return badD||badAspect||unknown;}
};
inline void addConeDebt(ConeDebt& debt,const TriangularConeQuality& q)
{
    ++debt.cones;
    if(!q.numeric)
    {
        ++debt.unknown;++debt.badD;++debt.badAspect;
        debt.minD=0;debt.maxAspect=GREAT;
        return;
    }
    if(q.determinant<.001)++debt.badD;
    if(q.aspect>1000)++debt.badAspect;
    debt.minD=min(debt.minD,q.determinant);
    debt.maxAspect=max(debt.maxAspect,q.aspect);
}
inline void addConeDebt(ConeDebt& debt,const ConeDebt& part)
{
    debt.complete=debt.complete&&part.complete;
    debt.cones+=part.cones;debt.badD+=part.badD;
    debt.badAspect+=part.badAspect;debt.unknown+=part.unknown;
    debt.minD=min(debt.minD,part.minD);
    debt.maxAspect=max(debt.maxAspect,part.maxAspect);
}
inline bool inheritsConeDebt(const ConeDebt& before,const ConeDebt& after,
    bool requireImprovement)
{
    if(!before.complete||!after.complete||!before.cones||!after.cones
      ||after.unknown||after.badD>before.badD||after.badAspect>before.badAspect)
        return false;
    if(before.badD&&after.minD<before.minD)return false;
    if(before.badAspect&&after.maxAspect>before.maxAspect)return false;
    return !requireImprovement||after.badD<before.badD
        ||after.badAspect<before.badAspect||after.unknown<before.unknown
        ||(before.badD&&after.minD>before.minD)
        ||(before.badAspect&&after.maxAspect<before.maxAspect);
}

// Non-coupled facet only. Coupled own-side invariance must be certified by the
// caller, as in currentC; never supply a physical-boundary surrogate for it.
// A native internal facet requires its real neighbour CC/V. For a physical
// boundary facet actualNeighbourCentre must be null; no dummy endpoint is read.
// Numeric roles survive a failed stock fan so the caller can preserve untouched
// pre-existing failures without making them a global prerequisite.
inline FacetQuality facetEndpoints
(
    const Face& row,
    const std::vector<point>& points,
    const FaceOF& faceOF,
    const point& actualOwnerCentre,
    scalar actualOwnerVolume,
    const point* actualNeighbourCentre,
    scalar actualNeighbourVolume,
    const Available& available,
    bool internal
)
{
    FacetQuality q;
    auto refuse=[&](const char* why)->FacetQuality { q.reason=why;return q; };
    if (!workAvailable(available)) return refuse("clock");
    if (!faceOF || !validRingIds(row,points,available)) return refuse("native-face");
    if (internal!=bool(actualNeighbourCentre)) return refuse("native-endpoints");
    vector sf(vector::zero);
    point fc(point::zero);
    faceOF(row.ring,sf,fc);
    if (!workAvailable(available)) return refuse("clock");
    const scalar ar=mag(sf);
    if (!finitePoint(sf) || !finitePoint(fc) || ar<=1e-12
        || !finitePoint(actualOwnerCentre) || !std::isfinite(actualOwnerVolume)
        || actualOwnerVolume<=VSMALL) return refuse("owner-of-geometry");
    vector d;
    if (internal)
    {
        const point& nei=*actualNeighbourCentre;
        if (!finitePoint(nei) || !std::isfinite(actualNeighbourVolume)
            || actualNeighbourVolume<=VSMALL) return refuse("neighbour-of-geometry");
        const scalar a=mag(sf&(fc-actualOwnerCentre)),b=mag(sf&(nei-fc));
        q.v[1]=min(a,b)/(a+b+VSMALL);
        q.v[2]=min(actualOwnerVolume,actualNeighbourVolume)
            /(max(actualOwnerVolume,actualNeighbourVolume)+VSMALL);
        d=nei-actualOwnerCentre;
        const scalar cosine=(sf&d)/(ar*mag(d)+ROOTVSMALL);
        q.v[4]=std::acos(max(scalar(-1),min(scalar(1),cosine)))
            *180/constant::mathematical::pi;
    }
    else
    {
        const vector n=sf/ar;
        d=n*(n&(fc-actualOwnerCentre));
        q.v[1]=q.v[2]=1;
    }
    const vector cpf=fc-actualOwnerCentre;
    const vector sv=cpf-((sf&cpf)/((sf&d)+ROOTVSMALL))*d;
    const vector direction=sv/(mag(sv)+ROOTVSMALL);
    scalar reach=(internal ? .2 : .4)*mag(d)+ROOTVSMALL;
    std::vector<point> ownerOrientedRing;
    ownerOrientedRing.reserve(row.ring.size());
    for (label p:row.ring)
    {
        if (!workAvailable(available)) return refuse("clock");
        reach=max(reach,mag(direction&(points[p]-fc)));
        ownerOrientedRing.push_back(points[p]);
    }
    q.v[3]=mag(sv)/reach;
    for (scalar value:q.v)
        if (!std::isfinite(value)) return refuse("facet-numeric");
    q.numeric=true;
    // Literal stock common anchor/edge admissibility, not the older proxy fan.
    // This helper uses polyMeshTetDecomposition::minTetQuality and one common
    // actual ring anchor for both endpoints. Do not weaken it here.
    if (!dcCartRetainedPlanarBaseStockTets::accept
        (ownerOrientedRing,fc,actualOwnerCentre,actualNeighbourCentre,
         available,&q.commonAnchor)) return refuse("stock-common-anchor-edge");
    if (!workAvailable(available)) return refuse("clock");
    q.valid=true;
    q.reason=nullptr;
    return q;
}

// Remote geometry is supplied only after a real processor source/subring
// match. It is not a local neighbour ID or a source/material certificate.
struct CoupledEndpoint
{
    point centre=point::zero;
    scalar volume=0;
};
inline FacetQuality facet
(
    const Face& row,
    const std::vector<point>& points,
    const FaceOF& faceOF,
    const point& actualOwnerCentre,
    scalar actualOwnerVolume,
    const point* actualNeighbourCentre,
    scalar actualNeighbourVolume,
    const Available& available
)
{
    return facetEndpoints(row,points,faceOF,actualOwnerCentre,actualOwnerVolume,
        actualNeighbourCentre,actualNeighbourVolume,available,row.neighbour>=0);
}
inline FacetQuality coupledFacet
(
    const Face& row,
    const std::vector<point>& points,
    const FaceOF& faceOF,
    const point& actualOwnerCentre,
    scalar actualOwnerVolume,
    const CoupledEndpoint& actualRemote,
    const Available& available
)
{
    if(row.neighbour!=-1||row.patch<0)
    {
        FacetQuality q;q.reason="coupled-native-endpoints";return q;
    }
    return facetEndpoints(row,points,faceOF,actualOwnerCentre,actualOwnerVolume,
        &actualRemote.centre,actualRemote.volume,available,true);
}

inline FacetQuality facet
(
    const Face& row,
    const std::vector<point>& points,
    const FaceOF& faceOF,
    const Cell& owner,
    const Cell* neighbour,
    const Available& available
)
{
    if (!owner.finite || owner.id!=row.owner
        || (row.neighbour>=0
            && (!neighbour || !neighbour->finite || neighbour->id!=row.neighbour))
        || (row.neighbour<0 && neighbour))
    {
        FacetQuality q;
        q.reason="native-cell-endpoints";
        return q;
    }
    return facet(row,points,faceOF,owner.centre,owner.volume,
        neighbour ? &neighbour->centre : nullptr,
        neighbour ? neighbour->volume : owner.volume,available);
}

inline const std::array<scalar,5>& thresholds()
{
    static const std::array<scalar,5> values{{.001,.05,.01,4-1e-8,70}};
    return values;
}

inline bool passes(scalar value,std::size_t metric)
{
    return metric<5 && std::isfinite(value)
        && (metric<3 ? value>=thresholds()[metric] : value<=thresholds()[metric]);
}

// New keeper geometry must be strict and pass D. AR follows its old role below,
// rather than imposing a new absolute prerequisite on a previously failing cell.
inline bool newHost(const Cell& cell)
{ return cell.finite && cell.strictGeometry && passes(cell.determinant,0); }

inline bool inheritsNumeric(scalar before,scalar after,std::size_t metric)
{
    if (metric>=5 || !std::isfinite(before) || !std::isfinite(after)) return false;
    if (passes(before,metric)) return passes(after,metric);
    return metric<3 ? after>=before*(1-1e-10) : after<=before*(1+1e-10);
}

inline bool inheritsAspect(scalar before,scalar after)
{
    return std::isfinite(before) && std::isfinite(after)
        && (before<=1000 ? after<=1000 : after<=before*(1+1e-10));
}

// Exact currentC agglomeration validity condition: changed/previously valid
// interfaces need a valid AFTER stock fan; an untouched pre-existing invalid fan
// retains that role. Numeric passing roles are always inherited individually.
inline bool inheritsFacet
(
    const FacetQuality& before,
    const FacetQuality& after,
    bool changedInterface
)
{
    if ((changedInterface || before.valid) && !after.valid) return false;
    for (std::size_t k=1;k<5;++k)
        if (!inheritsNumeric(before.v[k],after.v[k],k)) return false;
    return true;
}

}
}
#endif
