#ifndef dcCartOriginalNodeTensorBoxPartition_H
#define dcCartOriginalNodeTensorBoxPartition_H
#include "dcCartSharedAxisSeamVolumePartition.H"
#include <array>
#include <map>
#include <set>
#include <vector>
#include <algorithm>
#include <limits>
#include <stdexcept>
#include <functional>
#include <cmath>

namespace Foam { namespace dcCartOriginalNodeTensorBoxPartition {
namespace S=dcCartSharedAxisSeamVolumePartition;
using Index=std::array<std::size_t,3>;
using Tile=std::array<std::size_t,4>; // normal axis, grid-plane, first/second tangential interval
struct Result
{
    S::Plan plan;
    std::map<label,label> origin; // future native geometry row -> actual old native row; caps=-1
    std::map<label,std::vector<label>> coverage;
    std::array<std::vector<scalar>,3> coordinates;
    std::vector<Index> boxIndices;
    bool geometryOnly=true,sourceAuthority=false,peerAuthority=false;
    bool exact16Verified=false,fullStockAccepted=false;
};
struct SparsePoints
{
    const std::map<label,point>& values;
    std::size_t extent;
    std::size_t size()const{return extent;}
    const point& at(std::size_t i)const
    {
        if(i>std::size_t(std::numeric_limits<label>::max()))
            throw std::out_of_range("tensor point label");
        return values.at(label(i)); // absent points refuse, never zero-filled
    }
};
struct MeshView {const SparsePoints& points;};
inline bool build(label mother,label firstNewCell,label firstNewFace,
    const std::map<label,point>& nodes,const std::vector<S::Row>& boundary,
    const std::function<bool()>& work,Result& out,const char** why=nullptr)
{
    out=Result();if(why)*why=nullptr;
    const auto no=[&](const char* reason){out=Result();if(why)*why=reason;return false;};
    const auto available=[&](){return !work||work();};
    try
    {
        if(!available())return no("tensor-clock");
        if(mother<0||firstNewCell<=mother||firstNewFace<0
            ||nodes.size()<8||nodes.size()>512||boundary.size()<6||boundary.size()>512)
            return no("tensor-input-bound");
        Result r;std::set<label> referenced,nativeRows;
        for(const auto& node:nodes)
        {
            if(node.first<0||node.first==std::numeric_limits<label>::max()||!S::finite(node.second))
                return no("tensor-point");
            for(label axis=0;axis<3;++axis)r.coordinates[axis].push_back(node.second[axis]);
        }
        Index dimensions;
        std::size_t product=1,boxes=1;
        for(label axis=0;axis<3;++axis)
        {
            auto& values=r.coordinates[axis];std::sort(values.begin(),values.end());
            values.erase(std::unique(values.begin(),values.end()),values.end());
            if(values.size()<2||values.size()>65)return no("tensor-axis");
            dimensions[axis]=values.size();
            if(product>512/values.size()||boxes>64/(values.size()-1))return no("tensor-grid-bound");
            product*=values.size();boxes*=values.size()-1;
        }
        if(product!=nodes.size()||boxes>64)return no("tensor-grid-incomplete");
        if(boxes<2)return no("tensor-no-subdivision-needed");
        std::map<Index,label> pointAt;std::map<label,Index> indexOf;
        for(const auto& node:nodes)
        {
            Index at;
            for(label axis=0;axis<3;++axis)
            {
                const auto& values=r.coordinates[axis];
                at[axis]=std::lower_bound(values.begin(),values.end(),node.second[axis])-values.begin();
                if(at[axis]>=values.size()||values[at[axis]]!=node.second[axis])return no("tensor-coordinate");
            }
            if(!pointAt.emplace(at,node.first).second||!indexOf.emplace(node.first,at).second)
                return no("tensor-duplicate-position");
        }
        for(std::size_t x=0;x<dimensions[0];++x)for(std::size_t y=0;y<dimensions[1];++y)
            for(std::size_t z=0;z<dimensions[2];++z)
                if(!pointAt.count(Index{{x,y,z}}))return no("tensor-grid-incomplete");
        const auto pointId=[&](const Index& i){return pointAt.at(i);};
        const auto quad=[&](label axis,std::size_t plane,std::size_t a,std::size_t b,bool positive)
        {
            const label u=(axis+1)%3,v=(axis+2)%3;
            Index p{{0,0,0}};p[axis]=plane;p[u]=a;p[v]=b;
            std::vector<label> ring{pointId(p)};
            ++p[u];ring.push_back(pointId(p));++p[v];ring.push_back(pointId(p));
            --p[u];ring.push_back(pointId(p));
            if(!positive)std::reverse(ring.begin(),ring.end());
            return ring;
        };
        const auto expanded=[&](const std::vector<label>& ring)
        {
            std::multiset<std::pair<label,label>> edges;
            for(std::size_t i=0;i<ring.size();++i)
            {
                Index a=indexOf.at(ring[i]),b=indexOf.at(ring[(i+1)%ring.size()]);
                label axis=-1;
                for(label k=0;k<3;++k)if(a[k]!=b[k])
                {if(axis>=0)throw std::domain_error("tensor diagonal edge");axis=k;}
                if(axis<0)throw std::domain_error("tensor duplicate edge point");
                while(a!=b)
                {
                    Index next=a;
                    if(a[axis]<b[axis])++next[axis];else --next[axis];
                    edges.emplace(pointId(a),pointId(next));a=next;
                }
            }
            return edges;
        };
        std::map<Tile,std::size_t> boundaryTiles;
        for(std::size_t rowIndex=0;rowIndex<boundary.size();++rowIndex)
        {
            if(!available())return no("tensor-clock");
            const auto& row=boundary[rowIndex];
            if(row.native<0||row.native>=firstNewFace||!nativeRows.insert(row.native).second
                ||row.owner<0||row.owner>=firstNewCell||row.neighbour<-1||row.neighbour>=firstNewCell
                ||row.owner==row.neighbour||(row.owner==mother)==(row.neighbour==mother)
                ||row.ring.size()<4||row.ring.size()>64)
                return no("tensor-boundary-role-or-bound");
            std::set<label> unique;
            for(label p:row.ring)
            {
                if(!nodes.count(p)||!unique.insert(p).second)return no("tensor-unknown-or-repeated-point");
                referenced.insert(p);
            }
            label axis=-1;
            for(label k=0;k<3;++k)
            {
                bool fixed=true;const auto value=indexOf.at(row.ring.front())[k];
                for(label p:row.ring)fixed=fixed&&indexOf.at(p)[k]==value;
                if(fixed){if(axis>=0)return no("tensor-degenerate-face");axis=k;}
            }
            if(axis<0)return no("tensor-nonaxis-face");
            const auto plane=indexOf.at(row.ring.front())[axis];
            if(plane!=0&&plane!=dimensions[axis]-1)return no("tensor-interior-boundary");
            const label u=(axis+1)%3,v=(axis+2)%3;
            std::size_t loU=dimensions[u],loV=dimensions[v],hiU=0,hiV=0;
            for(label p:row.ring)
            {
                const auto& a=indexOf.at(p);loU=std::min(loU,a[u]);loV=std::min(loV,a[v]);
                hiU=std::max(hiU,a[u]);hiV=std::max(hiV,a[v]);
            }
            if(loU>=hiU||loV>=hiV)return no("tensor-empty-face");
            const auto vertex=[&](std::size_t a,std::size_t b)
            {Index p{{0,0,0}};p[axis]=plane;p[u]=a;p[v]=b;return pointId(p);};
            std::vector<label> expected{vertex(loU,loV),vertex(hiU,loV),vertex(hiU,hiV),vertex(loU,hiV)};
            const bool positive=plane==dimensions[axis]-1;
            if(!positive)std::reverse(expected.begin(),expected.end());
            auto actual=row.ring;if(row.owner!=mother)std::reverse(actual.begin(),actual.end());
            if(expanded(actual)!=expanded(expected))return no("tensor-nonrectangular-or-oriented-face");
            for(std::size_t a=loU;a<hiU;++a)for(std::size_t b=loV;b<hiV;++b)
                if(!boundaryTiles.emplace(Tile{{std::size_t(axis),plane,a,b}},rowIndex).second)
                    return no("tensor-overlapping-source-boundary");
        }
        if(referenced.size()!=nodes.size())return no("tensor-unreferenced-node");
        for(label axis=0;axis<3;++axis)
        {
            const label u=(axis+1)%3,v=(axis+2)%3;
            for(std::size_t side:std::array<std::size_t,2>{{0,dimensions[axis]-1}})
                for(std::size_t a=0;a+1<dimensions[u];++a)for(std::size_t b=0;b+1<dimensions[v];++b)
                    if(!boundaryTiles.count(Tile{{std::size_t(axis),side,a,b}}))
                        return no("tensor-incomplete-source-boundary");
        }
        if(boxes-1>std::size_t(std::numeric_limits<label>::max()-firstNewCell))
            return no("tensor-cell-label-overflow");
        std::map<Index,label> cellAt;std::size_t ordinal=0;
        for(std::size_t x=0;x+1<dimensions[0];++x)for(std::size_t y=0;y+1<dimensions[1];++y)
            for(std::size_t z=0;z+1<dimensions[2];++z)
            {
                Index b{{x,y,z}};const label cell=ordinal?firstNewCell+label(ordinal-1):mother;
                cellAt.emplace(b,cell);r.boxIndices.push_back(b);++ordinal;
            }
        std::map<Tile,label> rowAt;label nextFace=firstNewFace;
        const auto freshFace=[&]()
        {
            if(nextFace==std::numeric_limits<label>::max())throw std::overflow_error("tensor face label");
            return nextFace++;
        };
        S::Cell compound;compound.parent=mother;
        compound.child=boxes>1?firstNewCell:label(-1);
        for(const auto& b:r.boxIndices)
        {
            if(!available())return no("tensor-clock");
            S::Part part;part.cell=cellAt.at(b);
            for(label axis=0;axis<3;++axis)for(label direction=0;direction<2;++direction)
            {
                const auto plane=b[axis]+std::size_t(direction);
                const label u=(axis+1)%3,v=(axis+2)%3;
                const Tile tile{{std::size_t(axis),plane,b[u],b[v]}};
                const bool outer=plane==0||plane==dimensions[axis]-1;
                auto existing=rowAt.find(tile);
                if(existing==rowAt.end())
                {
                    S::Row row;label origin=-1;
                    if(outer)
                    {
                        const auto& old=boundary.at(boundaryTiles.at(tile));origin=old.native;
                        row=old;auto& pieces=r.coverage[old.native];
                        row.native=pieces.empty()?old.native:freshFace();pieces.push_back(row.native);
                        row.ring=quad(axis,plane,b[u],b[v],plane==dimensions[axis]-1);
                        if(old.owner!=mother)std::reverse(row.ring.begin(),row.ring.end());
                        if(row.owner==mother)row.owner=part.cell;else row.neighbour=part.cell;
                        // source is copied literally; unknown stays -1.
                    }
                    else
                    {
                        row.native=freshFace();row.source=-1;row.patch=row.zone=-1;
                        Index lower=b,upper=b;lower[axis]=plane-1;upper[axis]=plane;
                        row.owner=cellAt.at(lower);row.neighbour=cellAt.at(upper);
                        row.ring=quad(axis,plane,b[u],b[v],true);
                    }
                    r.origin.emplace(row.native,origin);
                    existing=rowAt.emplace(tile,r.plan.rows.size()).first;r.plan.rows.push_back(std::move(row));
                }
                const auto& row=r.plan.rows.at(existing->second);
                if(row.owner!=part.cell&&row.neighbour!=part.cell)return no("tensor-future-endpoint");
                S::Face face;face.source=row.source;face.ring=row.ring;face.forward=row.owner==part.cell;
                part.shell.push_back(std::move(face));
            }
            compound.parts.push_back(std::move(part));
        }
        if(r.coverage.size()!=boundary.size())return no("tensor-source-coverage");
        const SparsePoints sparse{nodes,std::size_t(nodes.rbegin()->first)+1};const MeshView mesh{sparse};
        const point reference=nodes.begin()->second;std::vector<S::Face> before;
        for(const auto& row:boundary)before.push_back(S::Face{row.source,row.ring,row.owner==mother});
        if(!S::geometry(mesh,r.plan,before,reference,work,compound.before))
            return no("tensor-before-geometry");
        for(auto& part:compound.parts)
            if(!S::geometry(mesh,r.plan,part.shell,reference,work,part.geometry))
                return no("tensor-part-geometry");
        // True C/D/AR, facet Stock, exact16 and VM are checked by the caller/control;
        // the original scalar thresholds/Source/peer authority are not replaced here.
        label splitAxis=-1;
        for(label axis=0;axis<3;++axis)if(dimensions[axis]>2)
        {if(splitAxis>=0){splitAxis=-2;break;}splitAxis=axis;}
        r.plan.axis=splitAxis>=0?splitAxis:label(-1);
        if(splitAxis>=0)r.plan.coordinate=r.coordinates[splitAxis][1];
        r.plan.cells.push_back(std::move(compound));r.plan.ready=true;
        if(!available())return no("tensor-clock");
        out=std::move(r);return true;
    }
    catch(const std::bad_alloc&){return no("tensor-allocation");}
    catch(const std::out_of_range&){return no("tensor-missing-input");}
    catch(const std::exception&){return no("tensor-invalid-topology");}
    catch(...){return no("tensor-unknown-exception");}
}
} }
#endif
