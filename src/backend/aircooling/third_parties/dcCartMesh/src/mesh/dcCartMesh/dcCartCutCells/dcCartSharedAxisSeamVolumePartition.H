#ifndef dcCartSharedAxisSeamVolumePartition_H
#define dcCartSharedAxisSeamVolumePartition_H
#include "dcCartPreparedFacetPartition.H"
#include "dcCartRetainedPlanarBaseStockTets.H"
#include "dcCartExactCutPlaneBacktracks.H"
#include "dcCartOriginalNodePositiveEarFacets.H"
#include "dcCartFiniteTensorCutScope.H"
#include <array>
#include <map>
#include <set>
#include <vector>
#include <algorithm>
#include <functional>
#include <limits>
#include <unordered_map>
#include <cmath>
#include <string>

namespace Foam { namespace dcCartSharedAxisSeamVolumePartition
{
namespace D=::dcCartPreparedFacetPartition;
namespace Finite=dcCartFiniteTensorCutScope;
using Edge=std::pair<label,label>;
struct Birth {label first=-1,second=-1;point position=point::zero;};
struct Face {label source=-1;std::vector<label> ring;bool forward=true;};
struct Geometry
{
    point centre=point::zero,lo=point::zero,hi=point::zero;
    scalar volume=0,minDepth=GREAT;label coplanarPairs=0;
    std::array<long double,4> physical{{0,0,0,0}};
};
struct Part
{
    label cell=-1;std::vector<Face> shell;Geometry geometry;
};
struct Cell
{
    label parent=-1,child=-1;std::vector<Face> positive,negative;
    std::vector<label> cap;Geometry before,a,b;
    // Empty for the original binary recipe; complete 1->N compound children otherwise.
    std::vector<Part> parts;
};
struct Row
{
    label native=-1,source=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    std::vector<label> ring;
};
struct Plan
{
    label axis=-1;scalar coordinate=0;
    std::vector<Birth> births;std::vector<Cell> cells;std::vector<Row> rows;
    bool ready=false;
    Finite::CutScope cutScope=Finite::CutScope::Propagating;
    std::vector<label> volumeRoots;
    // One actual producing plane for each birth, never inferred from axis above.
    std::vector<Finite::Plane> birthPlanes;
    std::map<label,std::shared_ptr<const Finite::SurfaceRefinement>> surfaceRefinements;
    // Value-owned preimages, not coupled/source or whole-cell authority.
    std::vector<Finite::SurfaceEdgeBirth> surfaceEdgeBirths;
    std::set<label> surfaceWholeParents;
};
inline std::size_t partCount(const Cell& cell)
{return cell.parts.empty()?2:cell.parts.size();}
inline label partCell(const Cell& cell,std::size_t ordinal)
{
    if(cell.parts.empty())return ordinal==0?cell.parent:cell.child;
    return cell.parts.at(ordinal).cell;
}
inline const Geometry& partGeometry(const Cell& cell,std::size_t ordinal)
{
    if(cell.parts.empty())return ordinal==0?cell.a:cell.b;
    return cell.parts.at(ordinal).geometry;
}
inline label addedCells(const Plan& plan)
{
    std::size_t count=0;
    for(const Cell& cell:plan.cells)
    {
        const std::size_t n=partCount(cell);
        if(n<2||n-1>std::size_t(std::numeric_limits<label>::max())-count)return -1;
        count+=n-1;
    }
    return label(count);
}
inline bool finite(const point& p)
{return std::isfinite(p.x())&&std::isfinite(p.y())&&std::isfinite(p.z());}
inline bool clock(const std::function<bool()>& available)
{return !available||available();}
template<class Mesh>
const point& coordinate(const Mesh& m,const Plan& plan,label id)
{
    if(id<label(m.points.size()))return m.points.at(id);
    return plan.births.at(std::size_t(id)-m.points.size()).position;
}
template<class Mesh>
void metric(const Mesh& m,const Plan& plan,const std::vector<label>& poly,vector& area,point& centre)
{
    // Literal faceAreaCentreOF arithmetic, with a sparse staged-point accessor.
    area=vector::zero;centre=point::zero;const label k=poly.size();
    if(k<3){for(label id:poly)centre+=coordinate(m,plan,id);if(k)centre/=scalar(k);return;}
    if(k==3)
    {
        const point& a=coordinate(m,plan,poly[0]);const point& b=coordinate(m,plan,poly[1]);
        const point& c=coordinate(m,plan,poly[2]);centre=(a+b+c)/3.0;area=.5*((b-a)^(c-a));return;
    }
    point fc=point::zero;for(label id:poly)fc+=coordinate(m,plan,id);fc/=scalar(k);
    vector sumN=vector::zero,sumAc=vector::zero;scalar sumA=0;
    for(label i=0;i<k;++i)
    {
        const point& p=coordinate(m,plan,poly[i]);const point& q=coordinate(m,plan,poly[(i+1)%k]);
        const vector n=(q-p)^(fc-p);const scalar a=mag(n);
        sumN+=n;sumA+=a;sumAc+=a*(p+q+fc);
    }
    if(sumA<ROOTVSMALL){centre=fc;return;}
    centre=(1.0/3.0)*sumAc/sumA;area=.5*sumN;
}
template<class Mesh>
bool geometry(const Mesh& m,const Plan& plan,const std::vector<Face>& faces,
 const point& reference,const std::function<bool()>& available,Geometry& out)
{
    out=Geometry();if(faces.size()<4||faces.size()>64)return false;
    std::map<Edge,std::pair<label,label>> edges;
    std::vector<vector> sf;std::vector<point> fc;point estimate=point::zero;
    scalar totalArea=0;vector closure=vector::zero;
    out.lo=point(GREAT,GREAT,GREAT);out.hi=point(-GREAT,-GREAT,-GREAT);
    for(const Face& f:faces)
    {
        if(!clock(available)||f.ring.size()<3||f.ring.size()>64)return false;
        std::set<label> unique;
        for(label id:f.ring)
        {
            const point& p=coordinate(m,plan,id);
            if(!finite(p)||!unique.insert(id).second)return false;
            out.lo=min(out.lo,p);out.hi=max(out.hi,p);
        }
        vector a;point c;metric(m,plan,f.ring,a,c);if(!f.forward)a=-a;
        if(!finite(a)||!finite(c)||mag(a)<=1e-12)return false;
        sf.push_back(a);fc.push_back(c);estimate+=c;closure+=a;totalArea+=mag(a);
        for(size_t j=0;j<f.ring.size();++j)
        {
            if(!clock(available))return false;
            label p=f.ring[j],q=f.ring[(j+1)%f.ring.size()];if(!f.forward)std::swap(p,q);
            auto& e=edges[std::minmax(p,q)];++e.first;e.second+=p<q?1:-1;
            const point& pa=coordinate(m,plan,p);const point& pb=coordinate(m,plan,q);
            std::array<long double,3> aa,bb,cc;
            for(label k=0;k<3;++k)
            {
                aa[k]=static_cast<long double>(pa[k])-reference[k];
                bb[k]=static_cast<long double>(pb[k])-reference[k];
                cc[k]=static_cast<long double>(c[k])-reference[k];
            }
            const long double v6=aa[0]*(bb[1]*cc[2]-bb[2]*cc[1])
                +aa[1]*(bb[2]*cc[0]-bb[0]*cc[2])+aa[2]*(bb[0]*cc[1]-bb[1]*cc[0]);
            const long double tv=v6/6;out.physical[0]+=tv;
            for(label k=0;k<3;++k)out.physical[k+1]+=tv*(aa[k]+bb[k]+cc[k])/4;
        }
    }
    for(const auto& e:edges)if(e.second.first!=2||e.second.second!=0)return false;
    if(totalArea<=VSMALL||mag(closure)/totalArea>1e-12)return false;
    estimate/=fc.size();scalar sum=0;point weighted=point::zero;
    for(size_t j=0;j<fc.size();++j)
    {const scalar p=sf[j]&(fc[j]-estimate);sum+=p;weighted+=p*(.75*fc[j]+.25*estimate);}
    if(!std::isfinite(sum)||sum<=VSMALL||!finite(weighted))return false;
    out.volume=sum/3;out.centre=weighted/sum;
    if(!finite(out.centre)||out.physical[0]<=0)return false;
    for(long double x:out.physical)if(!std::isfinite(x))return false;
    for(size_t j=0;j<fc.size();++j)
    {
        if(!clock(available))return false;
        const vector normal=sf[j]/mag(sf[j]);
        const scalar depth=normal&(fc[j]-out.centre);
        if(!std::isfinite(depth)||depth<=3e-6)return false;
        out.minDepth=min(out.minDepth,depth);
        for(size_t k=0;k<fc.size();++k)if(j!=k)
        {
            const vector d=fc[k]-fc[j];const scalar cosine=(normal&d)/max(mag(d),VSMALL);
            if(!std::isfinite(cosine))return false;if(cosine>-1e-6)++out.coplanarPairs;
        }
    }
    return clock(available);
}
using GridKey=std::array<long long,3>;
struct GridHash
{
    size_t operator()(const GridKey& k)const
    {
        size_t h=0;
        for(long long x:k)h^=std::hash<long long>{}(x)+size_t(0x9e3779b9)+(h<<6)+(h>>2);
        return h;
    }
};
struct PointIndex
{
    label initialPoints=0;
    std::vector<std::vector<label>> faces;
    std::unordered_map<GridKey,std::vector<label>,GridHash> grid;
};
inline bool gridKey(const point& p,GridKey& key)
{
    for(label k=0;k<3;++k)
    {
        const scalar q=std::floor(p[k]/3e-6);
        if(!std::isfinite(q)||q<=scalar(std::numeric_limits<long long>::min()+1)
           ||q>=scalar(std::numeric_limits<long long>::max()-1))return false;
        key[k]=static_cast<long long>(q);
    }
    return true;
}
// Optional draft-birth accelerator only. Binary power-of-two scaling is
// exact; a bucket is 2^-17 m wide, greater than twice the unchanged 3um radius.
inline bool draftBirthGridKey(const point& p,GridKey& key)
{
    if(sizeof(scalar)!=sizeof(double)||!std::numeric_limits<scalar>::is_iec559
        ||std::numeric_limits<scalar>::radix!=2
        ||std::numeric_limits<scalar>::digits!=53
        ||std::numeric_limits<scalar>::max_exponent!=1024
        ||std::numeric_limits<scalar>::has_denorm!=std::denorm_present
        ||!dcCartSignedCrossing::supportedFloatingEnvironment()||!finite(p))return false;
    for(label k=0;k<3;++k)
    {
        // A performance-only narrow domain: scaling is exact, finite and
        // within 2^47, with ample signed-key room for every neighbour.
        if(p[k]<scalar(-1073741824)||p[k]>scalar(1073741824))return false;
        const scalar q=std::floor(std::ldexp(p[k],17));
        if(!std::isfinite(q)||q<=scalar(std::numeric_limits<long long>::min()+1)
            ||q>=scalar(std::numeric_limits<long long>::max()-1))return false;
        key[k]=static_cast<long long>(q);
    }
    return true;
}
template<class Mesh>
bool pointIndex(const Mesh& m,const std::function<bool()>& available,PointIndex& out)
{
    out=PointIndex();
    if(m.points.size()>std::size_t(std::numeric_limits<label>::max()))return false;
    PointIndex index;index.initialPoints=m.points.size();index.faces.resize(m.points.size());
    for(label f=0;f<label(m.faces.size());++f)
    {
        if(!clock(available))return false;
        const label o=m.owner[f],n=m.neighbour[f];
        if(!m.faceAlive[f]||!((o>=0&&o<m.nCells&&m.cellAlive[o])
            ||(n>=0&&n<m.nCells&&m.cellAlive[n])))continue;
        for(label p:m.faces[f])
        {
            if(p<0||p>=index.initialPoints)return false;index.faces[p].push_back(f);
        }
    }
    index.grid.reserve(m.points.size());
    for(label p=0;p<index.initialPoints;++p)if(!index.faces[p].empty())
    {
        if(!clock(available)||!finite(m.points[p]))return false;
        GridKey key;if(!gridKey(m.points[p],key))return false;index.grid[key].push_back(p);
    }
    out=std::move(index);return true;
}
template<class Mesh>
bool mixed(const Mesh& m,const std::vector<label>& ring,label axis,scalar value)
{
    bool positive=false,negative=false;
    for(label p:ring){positive=positive||m.points[p][axis]>value;negative=negative||m.points[p][axis]<value;}
    return positive&&negative;
}
template<class Mesh>
std::vector<std::pair<label,scalar>> seams
(const Mesh& m,label parent,const std::vector<label>& cf,const std::function<bool()>& available)
{
    std::set<std::pair<label,scalar>> result;
    if(cf.size()<=6||cf.size()>64)return {};
    for(label f:cf)
    {
        if(f<0||f>=label(m.faces.size())||!m.faceAlive[f]
           ||(m.owner[f]!=parent&&m.neighbour[f]!=parent)
           ||m.faces[f].size()<3||m.faces[f].size()>64)return {};
        for(label p:m.faces[f])
            if(p<0||p>=label(m.points.size())||!finite(m.points[p]))return {};
    }
    for(size_t i=0;i<cf.size();++i)for(size_t j=0;j<i;++j)
    {
        if(!clock(available))return {};
        const label f=cf[i],g=cf[j];label support=-1;
        if(m.faces[f].size()<3||m.faces[g].size()<3)return {};
        for(label k=0;k<3;++k)
        {
            const scalar value=m.points[m.faces[f][0]][k];bool same=true;
            for(label p:m.faces[f])same=same&&m.points[p][k]==value;
            for(label p:m.faces[g])same=same&&m.points[p][k]==value;
            if(same)support=k;
        }
        if(support<0)continue;
        const label fn=m.owner[f]==parent?m.neighbour[f]:m.owner[f];
        const label gn=m.owner[g]==parent?m.neighbour[g]:m.owner[g];
        if(fn==gn&&m.patch[f]==m.patch[g]&&m.zone[f]==m.zone[g])continue;
        for(size_t a=0;a<m.faces[f].size();++a)for(size_t b=0;b<m.faces[g].size();++b)
        {
            label p=m.faces[f][a],q=m.faces[f][(a+1)%m.faces[f].size()];
            label r=m.faces[g][b],s=m.faces[g][(b+1)%m.faces[g].size()];
            if(m.owner[f]!=parent)std::swap(p,q);if(m.owner[g]!=parent)std::swap(r,s);
            if(p!=s||q!=r)continue;
            label along=-1;
            for(label k=0;k<3;++k)if(m.points[p][k]!=m.points[q][k])
            {if(along>=0){along=-2;break;}along=k;}
            if(along>=0&&along!=support)result.emplace(3-support-along,m.points[p][3-support-along]);
        }
    }
    return {result.begin(),result.end()};
}
// Exact original-edge interpolation, independently reusable by discovery,
// receive and geometry. No coordinate-only/native-label permission is inferred.
template<class Mesh>
bool exactSurfaceEdgeBirth(const Mesh& m,label a,label b,const Finite::Plane& plane,
 const std::function<bool()>& available,Birth& out)
{
    out=Birth();
    if(!clock(available)||!dcCartSignedCrossing::supportedFloatingEnvironment()
        ||a<0||b<0||a==b||std::size_t(a)>=m.points.size()
        ||std::size_t(b)>=m.points.size()||plane.first<0||plane.first>2
        ||!std::isfinite(plane.second)||!finite(m.points[a])||!finite(m.points[b]))return false;
    if(b<a)std::swap(a,b);
    const point& x=m.points[a];const point& y=m.points[b];
    const auto denominator=D::detail::number(y[plane.first])-D::detail::number(x[plane.first]);
    if(denominator==0)return false;
    const D::Rational t=(D::detail::number(plane.second)-D::detail::number(x[plane.first]))/denominator;
    if(!(t>0&&t<1))return false;
    Birth birth;birth.first=a;birth.second=b;
    for(label k=0;k<3;++k)
    {
        if(!clock(available))return false;
        birth.position[k]=k==plane.first?plane.second:D::detail::rounded
            (D::detail::number(x[k])+t*(D::detail::number(y[k])-D::detail::number(x[k])));
    }
    if(!finite(birth.position)||!clock(available))return false;
    out=birth;return true;
}
template<class Mesh>
bool resolveSurfaceEdgeBirth(const Mesh& m,label source,const Finite::Bits& first,
 const Finite::Bits& second,const Finite::Bits& birthBits,const Finite::Plane& plane,
 const std::function<bool()>& available,Finite::SurfaceEdgeBirth& out)
{
    out=Finite::SurfaceEdgeBirth();
    if(!clock(available)||!(first<second)||source<0||std::size_t(source)>=m.faces.size()
        ||!m.faceAlive[source]||m.faces[source].size()<3
        ||m.faces[source].size()>Finite::ringCap)return false;
    label found=0;Finite::SurfaceEdgeBirth staged;
    for(std::size_t j=0;j<m.faces[source].size();++j)
    {
        if(!clock(available))return false;
        label a=m.faces[source][j],b=m.faces[source][(j+1)%m.faces[source].size()];
        if(a<0||b<0||std::size_t(a)>=m.points.size()||std::size_t(b)>=m.points.size())return false;
        const auto x=Finite::pointBits(m.points[a]),y=Finite::pointBits(m.points[b]);
        if(!((x==first&&y==second)||(x==second&&y==first)))continue;
        Birth actual;if(!exactSurfaceEdgeBirth(m,a,b,plane,available,actual)
            ||Finite::pointBits(actual.position)!=birthBits||++found!=1)return false;
        staged.source=source;staged.first=actual.first;staged.second=actual.second;
        staged.firstBits=Finite::pointBits(m.points[staged.first]);
        staged.secondBits=Finite::pointBits(m.points[staged.second]);
        staged.birthBits=birthBits;staged.plane=plane;
    }
    if(found!=1)return false;out=staged;return true;
}
// Only original edges are accepted here. All live incident rings are refined,
// including walls and third processor sources; no endpoint becomes a volume root.
// Complete current-preimage, Source16, VM, both-end Stock and whole Q follow in
// Restore/Columns. Their authority is not replaced by this geometry preimage.
template<class Mesh>
bool prepareSurfaceEdgeBirths(const Mesh& m,const std::vector<std::vector<label>>& cf,
 label rank,const PointIndex& index,const std::vector<Finite::SurfaceEdgeBirth>& requests,
 const std::function<bool(label)>& blockedSource,const std::function<bool()>& available,
 Plan& out,std::string* why=nullptr)
{
    out=Plan();auto no=[&](const char* s){out=Plan();if(why)*why=s;return false;};
    if(requests.empty()||requests.size()>Finite::surfaceFaceCap
        ||index.initialPoints!=label(m.points.size())||index.faces.size()!=m.points.size()
        ||cf.size()<std::size_t(m.nCells))return no("surface-edge-input");
    Plan plan;plan.cutScope=Finite::CutScope::FiniteTensor;
    plan.axis=requests.front().plane.first;plan.coordinate=requests.front().plane.second;
    std::map<Edge,std::vector<std::pair<D::Rational,label>>> born;
    std::map<std::pair<Edge,Finite::Plane>,label> uniqueRequests;
    std::set<label> faces,parents,points;
    for(const auto& request:requests)
    {
        if(!clock(available))return no("clock");
        const label f=request.source,a=request.first,b=request.second;
        if(f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]
            ||m.faces[f].size()<3||m.faces[f].size()>Finite::ringCap
            ||a<0||a>=b||std::size_t(b)>=m.points.size()
            ||Finite::pointBits(m.points[a])!=request.firstBits
            ||Finite::pointBits(m.points[b])!=request.secondBits)return no("surface-edge-source");
        const Edge edge{a,b};bool onSource=false;
        for(std::size_t j=0;j<m.faces[f].size();++j)
            onSource=onSource||Edge(std::minmax(m.faces[f][j],
                m.faces[f][(j+1)%m.faces[f].size()]))==edge;
        Birth birth;
        if(!onSource||!exactSurfaceEdgeBirth(m,a,b,request.plane,available,birth)
            ||Finite::pointBits(birth.position)!=request.birthBits)return no("surface-edge-birth");
        plan.surfaceEdgeBirths.push_back(request);
        const auto key=std::make_pair(edge,request.plane);
        if(uniqueRequests.count(key))continue;
        GridKey grid;if(!gridKey(birth.position,grid))return no("birth-point");
        for(label x=-1;x<=1;++x)for(label y=-1;y<=1;++y)for(label z=-1;z<=1;++z)
        {
            const auto bucket=index.grid.find(GridKey{{grid[0]+x,grid[1]+y,grid[2]+z}});
            if(bucket==index.grid.end())continue;
            for(label p:bucket->second)
                if(!clock(available)||magSqr(birth.position-m.points[p])<=9e-12)
                    return no("active-point-3um");
        }
        for(const Birth& old:plan.births)
            if(!clock(available)||magSqr(birth.position-old.position)<=9e-12)
                return no("active-point-3um");
        if(plan.births.size()>=Finite::pointCap
            ||plan.births.size()>=std::size_t(std::numeric_limits<label>::max())-m.points.size())
            return no("finite-point-cap");
        const label id=m.points.size()+plan.births.size();
        uniqueRequests.emplace(key,id);plan.births.push_back(birth);
        plan.birthPlanes.push_back(request.plane);
        const auto den=D::detail::number(m.points[b][request.plane.first])
            -D::detail::number(m.points[a][request.plane.first]);
        const D::Rational parameter=(D::detail::number(request.plane.second)
            -D::detail::number(m.points[a][request.plane.first]))/den;
        born[edge].push_back({parameter,id});
        bool foundSource=false;
        for(label g:index.faces[a])
        {
            if(!clock(available)||g<0||std::size_t(g)>=m.faces.size()||!m.faceAlive[g])
                return no("surface-edge-star");
            bool contains=false;
            for(std::size_t j=0;j<m.faces[g].size();++j)
                contains=contains||Edge(std::minmax(m.faces[g][j],
                    m.faces[g][(j+1)%m.faces[g].size()]))==edge;
            if(!contains)continue;
            if(g==f)foundSource=true;
            if(m.faces[g].size()<3||m.faces[g].size()>Finite::ringCap
                ||(blockedSource&&blockedSource(g)))return no("surface-edge-star-source");
            faces.insert(g);if(faces.size()>Finite::surfaceFaceCap)return no("finite-surface-cap");
            for(label c:{m.owner[g],m.neighbour[g]})if(c>=0)
            {
                if(c>=m.nCells||!m.cellAlive[c]||m.cellRank[c]!=rank
                    ||cf[c].size()<4||cf[c].size()>64)return no("surface-edge-parent");
                parents.insert(c);if(parents.size()>Finite::haloParentCap)return no("finite-halo-parent-cap");
            }
        }
        if(!foundSource)return no("surface-edge-star-source");
    }
    for(auto& entry:born)
    {
        std::sort(entry.second.begin(),entry.second.end(),
            [](const auto& a,const auto& b){return a.first<b.first;});
        for(std::size_t j=1;j<entry.second.size();++j)
            if(entry.second[j-1].first>=entry.second[j].first)return no("surface-edge-order");
    }
    for(label c:parents)for(label f:cf[c])
    {
        if(!clock(available)||f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]
            ||(m.owner[f]!=c&&m.neighbour[f]!=c)||m.faces[f].size()<3
            ||m.faces[f].size()>Finite::ringCap)return no("surface-edge-context");
        for(label p:m.faces[f])
        {
            if(p<0||std::size_t(p)>=m.points.size()||!finite(m.points[p]))return no("surface-edge-point");
            points.insert(p);if(points.size()+plan.births.size()>Finite::pointCap)return no("finite-point-cap");
        }
    }
    for(label f:faces)
    {
        Row row;row.native=row.source=f;row.owner=m.owner[f];row.neighbour=m.neighbour[f];
        row.patch=m.patch[f];row.zone=m.zone[f];
        for(std::size_t j=0;j<m.faces[f].size();++j)
        {
            if(!clock(available))return no("clock");
            const label a=m.faces[f][j],b=m.faces[f][(j+1)%m.faces[f].size()];
            row.ring.push_back(a);const auto found=born.find(Edge(std::minmax(a,b)));
            if(found==born.end())continue;
            if(a<b)for(const auto& item:found->second)row.ring.push_back(item.second);
            else for(auto item=found->second.rbegin();item!=found->second.rend();++item)
                row.ring.push_back(item->second);
        }
        if(row.ring.size()>Finite::ringCap)return no("finite-halo-face-ring-cap");
        const auto at=[&](label p)->const point&{return coordinate(m,plan,p);};
        auto proof=Finite::SurfaceRefinementProducer::capture(m,f,row.ring,at,available);
        if(!proof||!plan.surfaceRefinements.emplace(f,std::move(proof)).second)return no("surface-edge-proof");
        plan.rows.push_back(std::move(row));
    }
    if(plan.rows.empty())return no("surface-edge-empty");
    plan.surfaceWholeParents=std::move(parents);
    if(!clock(available))return no("clock");
    plan.ready=true;out=std::move(plan);return true;
}
template<class Mesh>
bool prepare(const Mesh& m,const std::vector<std::vector<label>>& cf,
 label seed,label axis,scalar value,label rank,const PointIndex& pointIndex,
 const std::function<bool(label)>& coupled,
 const std::function<bool()>& available,Plan& out,std::string* why=nullptr,
 std::vector<label>* attempted=nullptr,
 const std::function<bool(label)>& selectedAllowed={},
 const std::function<void(label,const vector&,const vector&,const vector&)>& sourceAreaFailure={},
 bool preserveConvexSourceRings=false,
 const Finite::Request& cutScope=Finite::Request{},
 const std::function<bool(label)>& actualCoupled={})
{
    out=Plan();if(attempted)attempted->clear();auto refuse=[&](const char* reason){out=Plan();if(why)*why=reason;return false;};
    if(m.points.size()>std::size_t(std::numeric_limits<label>::max())
       ||m.faces.size()>std::size_t(std::numeric_limits<label>::max())
       ||axis<0||axis>2||seed<0||seed>=m.nCells||!std::isfinite(value))return refuse("input");
    const label initialCells=m.nCells,initialPoints=m.points.size(),initialFaces=m.faces.size();
    const bool finiteCut=cutScope.mode==Finite::CutScope::FiniteTensor;
    if((cutScope.mode!=Finite::CutScope::Propagating&&!finiteCut)
        ||(!finiteCut&&!cutScope.volumeParents.empty())
        ||(finiteCut&&(!actualCoupled||cutScope.volumeParents.empty()
            ||cutScope.volumeParents.size()>Finite::volumeParentCap||!cutScope.volumeParents.count(seed))))
        return refuse("finite-scope-input");
    auto aliveCell=[&](label c){return c>=0&&c<initialCells&&m.cellAlive[c];};
    std::set<label> selected;std::vector<label> queue{seed};
    if(finiteCut)queue.assign(cutScope.volumeParents.begin(),cutScope.volumeParents.end());
    std::set<Edge> expandedEdges;std::set<label> edgeQueued;
    for(size_t i=0;i<queue.size();++i)
    {
        if(!clock(available))return refuse("clock");
        const label c=queue[i];if(!selected.insert(c).second)continue;
        if(!aliveCell(c)||m.cellRank[c]!=rank||cf[c].size()<4||cf[c].size()>64)return refuse("parent");
        bool positive=false,negative=false;
        for(label f:cf[c])
        {
            if(f<0||f>=initialFaces||!m.faceAlive[f]||(m.owner[f]!=c&&m.neighbour[f]!=c)
               ||m.faces[f].size()<3||m.faces[f].size()>64)return refuse("incidence");
            if(coupled&&coupled(f))return refuse("coupled");
            for(label p:m.faces[f])
            {
                if(p<0||p>=initialPoints||!finite(m.points[p]))return refuse("point");
                positive=positive||m.points[p][axis]>value;negative=negative||m.points[p][axis]<value;
            }
            if(mixed(m,m.faces[f],axis,value))
            {
                const label n=m.owner[f]==c?m.neighbour[f]:m.owner[f];
                if(!finiteCut&&aliveCell(n)&&!selected.count(n))queue.push_back(n);
            }
            // A physical boundary can disconnect the face-neighbour graph
            // while retaining the same old edge on both sides. Every birth
            // on that edge must therefore include its complete live fan.
            for(size_t j=0;j<m.faces[f].size();++j)
            {
                const label a=m.faces[f][j],b=m.faces[f][(j+1)%m.faces[f].size()];
                const scalar x=m.points[a][axis],y=m.points[b][axis];
                if(!((x<value&&y>value)||(x>value&&y<value)))continue;
                const Edge e(std::minmax(a,b));
                if(!expandedEdges.insert(e).second)continue;
                if(e.first<0||e.second>=pointIndex.initialPoints
                   ||std::size_t(e.first)>=pointIndex.faces.size())return refuse("prior-cut-edge");
                for(label g:pointIndex.faces[e.first])
                {
                    if(!clock(available))return refuse("clock");
                    bool contains=false;
                    for(size_t k=0;k<m.faces[g].size();++k)
                        contains=contains||Edge(std::minmax(m.faces[g][k],m.faces[g][(k+1)%m.faces[g].size()]))==e;
                    if(!contains)continue;
                    for(label n:{m.owner[g],m.neighbour[g]})
                        if(!finiteCut&&aliveCell(n)&&!selected.count(n)&&edgeQueued.insert(n).second)queue.push_back(n);
                }
            }
        }
        if(!positive||!negative)return refuse("not-crossed");
    }
    if(attempted)attempted->assign(selected.begin(),selected.end());
    // Complete topology is fixed here. Every successful plan below has exactly
    // one cell per selected parent. The caller may reject an already partitioned
    // member before births/geometry; this callback grants no source permission.
    if(selectedAllowed)
        for(label c:selected)if(!selectedAllowed(c))return refuse("prior-partition");
    if(selected.size()>std::size_t(std::numeric_limits<label>::max()-initialCells))return refuse("cell-label");
    Plan plan;plan.axis=axis;plan.coordinate=value;
    if(finiteCut){plan.cutScope=cutScope.mode;plan.volumeRoots.assign(selected.begin(),selected.end());}
    std::set<label> faces;std::map<label,size_t> cellIndex;
    for(label c:selected)
    {
        cellIndex.emplace(c,plan.cells.size());Cell row;row.parent=c;
        row.child=initialCells+plan.cells.size();plan.cells.push_back(std::move(row));
        faces.insert(cf[c].begin(),cf[c].end());
    }
    std::set<Edge> cross;
    for(label f:faces)for(size_t j=0;j<m.faces[f].size();++j)
    {
        const label a=m.faces[f][j],b=m.faces[f][(j+1)%m.faces[f].size()];
        const scalar x=m.points[a][axis],y=m.points[b][axis];
        if((x<value&&y>value)||(x>value&&y<value))cross.insert(std::minmax(a,b));
    }
    // Cached incidence covers actual live faces at entry. This pass never
    // drops an old outline node or moves an old point. A later disjoint
    // component may use only those original edges; no prior cut is recut.
    std::set<label> haloOnlyFaces,haloParents;
    for(const Edge& e:cross)
    {
        if(e.first<0||e.second>=pointIndex.initialPoints)return refuse("prior-cut-edge");
        for(label f:pointIndex.faces[e.first])
        {
            if(!clock(available))return refuse("clock");
            bool contains=false;
            for(size_t j=0;j<m.faces[f].size();++j)
                contains=contains||Edge(std::minmax(m.faces[f][j],m.faces[f][(j+1)%m.faces[f].size()]))==e;
            if(!contains)continue;
            if(finiteCut)
            {
                if(f<0||f>=initialFaces||!m.faceAlive[f]||m.faces[f].size()<3
                    ||m.faces[f].size()>Finite::ringCap)return refuse("finite-halo-face");
                if(!faces.count(f))
                {
                    if(actualCoupled(f))return refuse("finite-unresolved-coupled-halo");
                    haloOnlyFaces.insert(f);
                }
                for(label c:{m.owner[f],m.neighbour[f]})if(c>=0)
                {
                    if(!aliveCell(c)||m.cellRank[c]!=rank||cf[c].size()<4||cf[c].size()>64)
                        return refuse("finite-halo-parent");
                    haloParents.insert(c);
                    if(haloParents.size()>Finite::haloParentCap)return refuse("finite-halo-parent-cap");
                }
            }
            else if(!faces.count(f)||(aliveCell(m.owner[f])&&!selected.count(m.owner[f]))
               ||(aliveCell(m.neighbour[f])&&!selected.count(m.neighbour[f])))return refuse("edge-star");
        }
    }
    std::size_t finitePointCount=0;
    if(finiteCut)
    {
        faces.insert(haloOnlyFaces.begin(),haloOnlyFaces.end());
        if(faces.size()>Finite::surfaceFaceCap)return refuse("finite-surface-cap");
        std::set<label> surfacePoints;
        for(label f:faces)for(label p:m.faces[f])
        {
            if(!clock(available)||p<0||p>=initialPoints||!finite(m.points[p]))return refuse("finite-halo-point");
            surfacePoints.insert(p);
            if(surfacePoints.size()>Finite::pointCap)return refuse("finite-point-cap");
        }
        finitePointCount=surfacePoints.size();
    }
    std::map<Edge,label> born;
    std::unordered_map<GridKey,std::vector<label>,GridHash> draftBirthGrid;
    std::vector<label> draftBirthCandidates;
    bool draftBirthGridReady=true;
    for(const Edge& e:cross)
    {
        if(!clock(available)||!dcCartSignedCrossing::supportedFloatingEnvironment())return refuse("clock-or-fp");
        if(plan.births.size()>=std::size_t(std::numeric_limits<label>::max()-initialPoints))return refuse("point-label");
        if(finiteCut&&plan.births.size()>=Finite::pointCap-finitePointCount)return refuse("finite-point-cap");
        const point& a=m.points[e.first];const point& b=m.points[e.second];
        const D::Rational t=(D::detail::number(value)-D::detail::number(a[axis]))
            /(D::detail::number(b[axis])-D::detail::number(a[axis]));
        if(!(t>0&&t<1))return refuse("edge-parameter");
        Birth birth;birth.first=e.first;birth.second=e.second;
        for(label k=0;k<3;++k)
        {
            if(!clock(available))return refuse("clock");
            birth.position[k]=k==axis?value:D::detail::rounded(D::detail::number(a[k])
                +t*(D::detail::number(b[k])-D::detail::number(a[k])));
        }
        GridKey key;if(!finite(birth.position)||!gridKey(birth.position,key))return refuse("birth-point");
        for(label x=-1;x<=1;++x)for(label y=-1;y<=1;++y)for(label z=-1;z<=1;++z)
        {
            const auto it=pointIndex.grid.find(GridKey{{key[0]+x,key[1]+y,key[2]+z}});
            if(it==pointIndex.grid.end())continue;
            for(label p:it->second)if(magSqr(birth.position-coordinate(m,plan,p))<=9e-12)
                return refuse("active-point-3um");
        }
        // Previous accepted edge births and this draft's births remain active.
        for(label p=pointIndex.initialPoints;p<initialPoints;++p)
            if(magSqr(birth.position-m.points[p])<=9e-12)return refuse("active-point-3um");
        GridKey draftKey;
        if(draftBirthGridReady&&!draftBirthGridKey(birth.position,draftKey))
            draftBirthGridReady=false;
        if(draftBirthGridReady)
        {
            draftBirthCandidates.clear();
            for(label x=-1;x<=1;++x)for(label y=-1;y<=1;++y)for(label z=-1;z<=1;++z)
            {
                const auto it=draftBirthGrid.find
                    (GridKey{{draftKey[0]+x,draftKey[1]+y,draftKey[2]+z}});
                if(it!=draftBirthGrid.end())draftBirthCandidates.insert
                    (draftBirthCandidates.end(),it->second.begin(),it->second.end());
            }
            // Compare candidates in the same accepted-birth order as the
            // literal scan. This index grants neither a merge nor a new point.
            std::sort(draftBirthCandidates.begin(),draftBirthCandidates.end());
            for(label ordinal:draftBirthCandidates)
                if(magSqr(birth.position-plan.births[ordinal].position)<=9e-12)
                    return refuse("active-point-3um");
        }
        else
            for(const Birth& old:plan.births)
                if(magSqr(birth.position-old.position)<=9e-12)return refuse("active-point-3um");
        const label id=initialPoints+plan.births.size();
        born.emplace(e,id);plan.births.push_back(birth);
        if(finiteCut)plan.birthPlanes.emplace_back(axis,value);
        if(draftBirthGridReady)draftBirthGrid[draftKey].push_back(plan.births.size()-1);
    }
    struct Split {std::vector<label> positive,negative,haloOutline;std::vector<std::vector<label>> positivePieces,negativePieces,onPieces;bool pos=false,neg=false,haloOnly=false;};
    std::map<label,Split> split;
    for(label f:faces)
    {
        if(!clock(available))return refuse("clock");
        Split s;
        if(finiteCut&&haloOnlyFaces.count(f))
        {
            s.haloOnly=true;std::vector<label> refined;
            for(size_t j=0;j<m.faces[f].size();++j)
            {
                const label a=m.faces[f][j],b=m.faces[f][(j+1)%m.faces[f].size()];
                refined.push_back(a);const auto birth=born.find(std::minmax(a,b));
                if(birth!=born.end())refined.push_back(birth->second);
            }
            if(refined==m.faces[f]||refined.size()>Finite::ringCap)return refuse("finite-halo-refinement");
            // Retain the full directed outline, including every true cut birth.
            // Positive existing-node pieces give each straight-edge segment its
            // own future cone sector; they do not grant Source or Stock.
            s.haloOutline=refined;
            const auto at=[&](label p)->const point&{return coordinate(m,plan,p);};
            const auto sourceMetric=[&](const std::vector<label>& ring,vector& a,point& c)
                {metric(m,plan,ring,a,c);};
            bool changed=false;std::string partitionWhy;
            if(!dcCartOriginalNodePositiveEarFacets::partition(refined,at,sourceMetric,
                available,s.onPieces,changed,&partitionWhy,false))
                return refuse(partitionWhy.empty()?"finite-halo-positive-ears":partitionWhy.c_str());
            if(s.onPieces.empty()||s.onPieces.size()>Finite::ringCap)
                return refuse("finite-halo-piece-cap");
            split.emplace(f,std::move(s));continue;
        }
        for(size_t j=0;j<m.faces[f].size();++j)
        {
            const label a=m.faces[f][j],b=m.faces[f][(j+1)%m.faces[f].size()];
            const scalar x=m.points[a][axis],y=m.points[b][axis];
            s.pos=s.pos||x>value;s.neg=s.neg||x<value;
            if(x>=value)s.positive.push_back(a);if(x<=value)s.negative.push_back(a);
            if((x<value&&y>value)||(x>value&&y<value))
            {
                const label p=born.at(std::minmax(a,b));s.positive.push_back(p);s.negative.push_back(p);
            }
        }
        if((s.pos&&s.positive.size()<3)||(s.neg&&s.negative.size()<3))return refuse("split-face");
        if(s.pos&&s.neg)
        {
            dcCartExactCutPlaneBacktracks::Result backtracks;std::string backtrackWhy;
            const auto at=[&](label p)->const point&{return coordinate(m,plan,p);};
            const auto sourceCohort=[&](label p)->bool
            {
                if(p<0||p>=pointIndex.initialPoints||std::size_t(p)>=pointIndex.faces.size())return false;
                bool currentSource=false,otherSource=false;
                for(label g:pointIndex.faces[p])
                {
                    if(!clock(available)||g<0||g>=initialFaces||!m.faceAlive[g])return false;
                    if(std::find(m.faces[g].begin(),m.faces[g].end(),p)==m.faces[g].end())return false;
                    currentSource=currentSource||g==f;otherSource=otherSource||g!=f;
                }
                return currentSource&&otherSource;
            };
            if(!dcCartExactCutPlaneBacktracks::normalize(m.faces[f],axis,value,at,
                sourceCohort,available,s.positive,s.negative,backtracks,&backtrackWhy))
                return refuse("source-cut-backtrack");
            vector a,b,old;point ca,cb,co;
            metric(m,plan,s.positive,a,ca);metric(m,plan,s.negative,b,cb);metric(m,plan,m.faces[f],old,co);
            if(mag(a)<=1e-12||mag(b)<=1e-12||(a&old)<=0||(b&old)<=0
               ||mag(a+b-old)/max(mag(old),VSMALL)>1e-10
               ||mag(mag(a)+mag(b)-mag(old))/max(mag(old),VSMALL)>1e-10)
            {
                // Observe only this already-decided refusal. Copy no geometry,
                // perform no additional floating arithmetic or clock calls.
                if(sourceAreaFailure)try{sourceAreaFailure(f,old,a,b);}catch(...){}
                return refuse("source-area");
            }
        }
        const auto sourcePoint=[&](label p)->const point&{return coordinate(m,plan,p);};
        const auto sourceMetric=[&](const std::vector<label>& ring,vector& area,point& centre)
            {metric(m,plan,ring,area,centre);};
        auto positivePieces=[&](const std::vector<label>& ring,std::vector<std::vector<label>>& pieces)
        {
            bool changed=false;std::string why;
            return dcCartOriginalNodePositiveEarFacets::partition(ring,sourcePoint,
                sourceMetric,available,pieces,changed,&why,preserveConvexSourceRings);
        };
        if((s.pos&&!positivePieces(s.positive,s.positivePieces))
            ||(s.neg&&!positivePieces(s.negative,s.negativePieces))
            ||(!s.pos&&!s.neg&&!positivePieces(m.faces[f],s.onPieces)))
            return refuse("source-positive-ear-facets");
        split.emplace(f,std::move(s));
    }
    std::map<label,Geometry> outside;
    for(Cell& c:plan.cells)
    {
        std::vector<Face> original;
        for(label f:cf[c.parent])
        {
            const bool forward=m.owner[f]==c.parent;original.push_back({f,m.faces[f],forward});
            const Split& s=split.at(f);
            if(!s.pos&&!s.neg)
            {
                vector area;point fc;metric(m,plan,m.faces[f],area,fc);if(!forward)area=-area;
                if(mag(area)<=1e-12||std::abs(area[axis])/mag(area)<1-1e-12)return refuse("on-face");
                auto& side=area[axis]<0?c.positive:c.negative;
                for(const auto& piece:s.onPieces)side.push_back({f,piece,forward});
            }
            else
            {
                if(s.pos)for(const auto& piece:s.positivePieces)c.positive.push_back({f,piece,forward});
                if(s.neg)for(const auto& piece:s.negativePieces)c.negative.push_back({f,piece,forward});
            }
        }
        point reference=point::zero;
        for(const Face& f:original)for(label id:f.ring)reference+=m.points[id];
        size_t nr=0;for(const Face& f:original)nr+=f.ring.size();reference/=scalar(nr);
        if(!geometry(m,plan,original,reference,available,c.before))return refuse("old-geometry");
        std::map<Edge,label> remaining;
        for(const Face& f:c.positive)for(size_t j=0;j<f.ring.size();++j)
        {
            label a=f.ring[j],b=f.ring[(j+1)%f.ring.size()];
            if(coordinate(m,plan,a)[axis]!=value||coordinate(m,plan,b)[axis]!=value)continue;
            if(f.forward)std::swap(a,b);
            const Edge edge{a,b},reverse{b,a};auto it=remaining.find(reverse);
            if(it!=remaining.end()){if(--it->second==0)remaining.erase(it);}
            else ++remaining[edge];
        }
        std::map<label,label> next;std::map<label,label> incoming;
        for(const auto& e:remaining)
            if(e.second!=1||!next.emplace(e.first.first,e.first.second).second||++incoming[e.first.second]!=1)
                return refuse("cap-degree");
        if(next.size()<3||next.size()>64)return refuse("cap-size");
        label p=next.begin()->first;
        for(size_t j=0;j<next.size();++j)
        {
            if(!next.count(p)||std::find(c.cap.begin(),c.cap.end(),p)!=c.cap.end())return refuse("cap-cycle");
            c.cap.push_back(p);p=next.at(p);
        }
        if(p!=c.cap[0])return refuse("cap-cycle");
        c.positive.push_back({-1,c.cap,true});c.negative.push_back({-1,c.cap,false});
        if(!geometry(m,plan,c.positive,reference,available,c.a)
           ||!geometry(m,plan,c.negative,reference,available,c.b))return refuse("part-geometry");
        const scalar span=max(mag(c.before.hi-c.before.lo),scalar(1e-6));
        if(mag(c.a.volume+c.b.volume-c.before.volume)/max(c.before.volume,VSMALL)>1e-10)
            return refuse("volume");
        for(label k=0;k<4;++k)
        {
            const long double scale=c.before.physical[0]*(k?span:1);
            if(std::abs(c.a.physical[k]+c.b.physical[k]-c.before.physical[k])
               /std::max(scale,static_cast<long double>(VSMALL))>1e-10L)return refuse("physical-moment");
        }
    }
    auto endpoint=[&](label parent,bool positive,label& id,point& cc)
    {
        if(parent<0){id=-1;return true;}
        const auto it=cellIndex.find(parent);
        if(it!=cellIndex.end())
        {
            const Cell& c=plan.cells[it->second];id=positive?c.parent:c.child;cc=positive?c.a.centre:c.b.centre;return true;
        }
        if(!aliveCell(parent))return false;
        if(finiteCut&&(m.cellRank[parent]!=rank||cf[parent].size()<4||cf[parent].size()>64))return false;
        id=parent;auto found=outside.find(parent);
        if(found==outside.end())
        {
            std::vector<Face> shell;
            for(label f:cf[parent])
            {
                const bool forward=m.owner[f]==parent;const auto staged=split.find(f);
                if(staged==split.end()){shell.push_back({f,m.faces[f],forward});continue;}
                const Split& s=staged->second;
                // Finite volume roots retain this outside cell with both true
                // shared-face pieces. Propagating preparation keeps its old gate.
                if(s.pos&&s.neg)
                {
                    if(!finiteCut)return false;
                    for(const auto& piece:s.positivePieces)shell.push_back({f,piece,forward});
                    for(const auto& piece:s.negativePieces)shell.push_back({f,piece,forward});
                }
                else
                {
                    const auto& pieces=!s.pos&&!s.neg?s.onPieces:s.pos?s.positivePieces:s.negativePieces;
                    for(const auto& piece:pieces)shell.push_back({f,piece,forward});
                }
            }
            // Complete surviving source pieces define this real future outside.
            // Its old shell is not required to self-heal. Obtain literal OF V/CC;
            // every affected shared piece is tested below with this actual CC.
            Geometry g;point estimate=point::zero;std::vector<vector> sf;std::vector<point> fc;
            if(shell.empty())return false;
            for(const Face& f:shell)
            {
                if(!clock(available))return false;
                vector a;point centre;metric(m,plan,f.ring,a,centre);
                if(!finite(a)||!finite(centre))return false;
                sf.push_back(f.forward?a:-a);fc.push_back(centre);estimate+=centre;
            }
            estimate/=fc.size();scalar sum=0;point weighted=point::zero;
            for(size_t j=0;j<fc.size();++j)
            {const scalar p=sf[j]&(fc[j]-estimate);sum+=p;weighted+=p*(.75*fc[j]+.25*estimate);}
            if(!std::isfinite(sum)||sum<=VSMALL||!finite(weighted))return false;
            g.volume=sum/3;g.centre=weighted/sum;if(!finite(g.centre))return false;
            found=outside.emplace(parent,g).first;
        }
        cc=found->second.centre;return true;
    };
    if(faces.size()+plan.cells.size()>std::size_t(std::numeric_limits<label>::max()-initialFaces))
        return refuse("face-label");
    std::size_t sourceRows=0;
    for(const auto& entry:split)
    {
        const Split& s=entry.second;
        const std::size_t count=!s.pos&&!s.neg?s.onPieces.size():s.positivePieces.size()+s.negativePieces.size();
        if(!clock(available)||!count||count>std::size_t(std::numeric_limits<label>::max())-sourceRows)
            return refuse("face-label");
        sourceRows+=count;
    }
    if(sourceRows<faces.size()||sourceRows-faces.size()+plan.cells.size()
        >std::size_t(std::numeric_limits<label>::max()-initialFaces))return refuse("face-label");
    if(finiteCut&&(sourceRows>Finite::surfaceFaceCap
        ||plan.cells.size()>Finite::surfaceFaceCap-sourceRows))return refuse("finite-row-cap");
    label nextFace=initialFaces;
    for(label f:faces)
    {
        const Split& s=split.at(f);const bool on=!s.haloOnly&&!s.pos&&!s.neg;
        auto makeRow=[&](bool positive,const std::vector<label>& ring,label native)->bool
        {
            Row r;r.native=native;r.source=f;r.ring=ring;r.patch=m.patch[f];r.zone=m.zone[f];
            point own,nei;bool ownPositive=positive,neiPositive=positive;
            if(on)
            {
                vector a;point fc;metric(m,plan,ring,a,fc);
                ownPositive=a[axis]<0;neiPositive=a[axis]>0;
            }
            if(!endpoint(m.owner[f],ownPositive,r.owner,own)
               ||!endpoint(m.neighbour[f],neiPositive,r.neighbour,nei))return false;
            vector a;point fc;metric(m,plan,ring,a,fc);std::vector<point> points;
            for(label id:ring)points.push_back(coordinate(m,plan,id));
            if(!dcCartRetainedPlanarBaseStockTets::accept
                (points,fc,own,r.neighbour>=0?&nei:nullptr,available))return false;
            plan.rows.push_back(std::move(r));return true;
        };
        bool originalSlotUsed=false;
        auto makePieces=[&](bool positive,const std::vector<std::vector<label>>& pieces)
        {
            for(const auto& piece:pieces)
            {
                if(!clock(available)||(!originalSlotUsed&&piece.empty())
                    ||(originalSlotUsed&&nextFace==std::numeric_limits<label>::max()))return false;
                const label native=originalSlotUsed?nextFace++:f;originalSlotUsed=true;
                if(!makeRow(positive,piece,native))return false;
            }
            return true;
        };
        if(s.haloOnly)
        {
            const std::size_t firstRow=plan.rows.size();
            if(!makePieces(true,s.onPieces))return refuse("finite-halo-stock");
            std::map<label,std::vector<label>> actualPieces;
            for(std::size_t j=firstRow;j<plan.rows.size();++j)
            {
                const auto& row=plan.rows[j];
                if(!clock(available)||row.source!=f||row.owner!=m.owner[f]
                    ||row.neighbour!=m.neighbour[f]||row.patch!=m.patch[f]||row.zone!=m.zone[f]
                    ||!actualPieces.emplace(row.native,row.ring).second)
                    return refuse("finite-halo-piece-binding");
            }
            const auto at=[&](label p)->const point&{return coordinate(m,plan,p);};
            auto proof=Finite::SurfaceRefinementProducer::capture(m,f,s.haloOutline,actualPieces,at,available);
            if(!proof||!plan.surfaceRefinements.emplace(f,std::move(proof)).second)
                return refuse("finite-halo-proof");
        }
        else if(on){if(!makePieces(true,s.onPieces))return refuse("stock-on");}
        else
        {
            if(s.pos&&!makePieces(true,s.positivePieces))return refuse("stock-positive");
            if(s.neg&&!makePieces(false,s.negativePieces))return refuse("stock-negative");
        }
    }
    for(const Cell& c:plan.cells)
    {
        std::vector<point> points;for(label p:c.cap)points.push_back(coordinate(m,plan,p));
        vector a;point fc;metric(m,plan,c.cap,a,fc);
        if(!dcCartRetainedPlanarBaseStockTets::accept(points,fc,c.a.centre,&c.b.centre,available))
            return refuse("stock-cap");
        Row r;r.native=nextFace++;r.owner=c.parent;r.neighbour=c.child;r.ring=c.cap;plan.rows.push_back(std::move(r));
    }
    if(!clock(available))return refuse("clock");
    plan.ready=true;out=std::move(plan);return true;
}
} }
#endif
