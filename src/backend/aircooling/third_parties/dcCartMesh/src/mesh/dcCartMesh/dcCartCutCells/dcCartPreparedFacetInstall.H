#ifndef dcCartPreparedFacetInstall_H
#define dcCartPreparedFacetInstall_H
#include "dcCartPreparedFacetContext.H"
#include "dcCartPreparedFacetBirth.H"
#include "primitiveMeshTools.H"
#include "faceList.H"
#include "pointField.H"
#include <limits>
#include <type_traits>
#include <chrono>
#include <cstdlib>
#include <cstdio>
#include <cstring>
namespace dcCartPreparedFacetInstall
{
namespace D=dcCartPreparedFacetPartition;
namespace X=dcCartPreparedFacetPartition::detail;
using Label=int;
struct OriginalFaceReceipt
{
    Label parentOriginalFace=-1,expectedPatch=-1,expectedZone=-1;
};
using OriginalFaceReceipts=std::map<Label,OriginalFaceReceipt>;
// Optional observation only: first short-circuit protected-original condition.
struct ProtectedOriginalFaceDiagnostic
{
    bool hit=false;unsigned condition=0;
    std::size_t ordinal=0;
    D::Id seed=-1,face=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
};
using Ring=std::vector<Label>;
using Edge=std::pair<Label,Label>;
struct Refusal:std::runtime_error { explicit Refusal(const char* s):std::runtime_error(s){} };
// Explicit caller binding to the HOST snapshot, separate from the local 2M work cap.
struct HostLimits {std::size_t faces=500000,points=500000,cells=500000;};
template<class Mesh>HostLimits inputSizedHostLimits(const Mesh& m)
{return {m.faces.size(),m.points.size(),std::size_t(m.nCells)};}
inline std::size_t hostScanBudget(const HostLimits& host)
{
    const std::size_t maximum=std::size_t(std::numeric_limits<Label>::max());
    if(host.faces>maximum||host.points>maximum||host.cells>maximum)
        throw Refusal("install-host-index-cap");
    if(host.faces>(std::numeric_limits<std::size_t>::max()-host.points)/5)
        throw Refusal("install-host-scan-overflow");
    return 5*host.faces+host.points;
}
inline Ring cycle(const Ring& r)
{
    if(r.empty())throw Refusal("empty-ring");
    Ring best=r;
    for(std::size_t j=1;j<r.size();++j)
    {Ring s;for(std::size_t k=0;k<r.size();++k)s.push_back(r[(j+k)%r.size()]);if(s<best)best=std::move(s);}
    return best;
}
inline Ring reverse(Ring r){std::reverse(r.begin(),r.end());return r;}
inline Ring key(const Ring& r){return std::min(cycle(r),cycle(reverse(r)));}
inline bool sameBits(double a,double b)
{std::uint64_t x=0,y=0;std::memcpy(&x,&a,8);std::memcpy(&y,&b,8);return x==y;}
inline bool onSegment(const D::ExactPoint& p,const D::ExactPoint& a,const D::ExactPoint& b,D::Rational& t)
{
    const auto e=X::sub(b,a),v=X::sub(p,a);if(!X::nonzero(e)||X::nonzero(X::cross(e,v)))return false;
    unsigned d=0;while(e[d]==0)++d;t=v[d]/e[d];return t>0&&t<1;
}
struct Geometry {double volume=0;Foam::point centre=Foam::point::zero;};
// Use the installed OpenFOAM face arithmetic. Cell accumulation below follows
// primitiveMeshTools::makeCellCentresAndVols, with the same signed pyramids.
template<class Mesh,class Tick>
std::map<Label,Geometry> geometry(const Mesh& m,const std::set<Label>& cells,Tick tick,
                                  const Foam::point* reference=nullptr,
                                  const std::function<void()>& hostScan=std::function<void()>())
{
    auto finite=[](const Foam::vector& p)
    {return std::isfinite(p[0])&&std::isfinite(p[1])&&std::isfinite(p[2]);};
    if(reference&&!finite(*reference))throw Refusal("nonfinite-geometry-reference");
    std::vector<Label> selected;std::map<Label,Label> pointIds;
    for(std::size_t f=0;f<m.faces.size();++f)
    {
        if(hostScan)hostScan();else tick();if(!m.faceAlive[f]||(!cells.count(m.owner[f])&&!cells.count(m.neighbour[f])))continue;
        selected.push_back(Label(f));
        for(Label p:m.faces[f])if(!pointIds.count(p))pointIds[p]=Label(pointIds.size());
    }
    Foam::pointField points(pointIds.size());
    for(const auto& p:pointIds)
    {
        points[p.second]=reference?m.points[p.first]-*reference:m.points[p.first];
        if(!finite(points[p.second]))throw Refusal("nonfinite-local-geometry-point");
    }
    Foam::faceList faces(selected.size());
    for(std::size_t i=0;i<selected.size();++i)
    {const auto& r=m.faces[selected[i]];faces[i].resize(r.size());for(std::size_t j=0;j<r.size();++j)faces[i][j]=pointIds.at(r[j]);}
    Foam::vectorField fc,fa;Foam::primitiveMeshTools::makeFaceCentresAndAreas(faces,points,fc,fa);tick();
    for(std::size_t i=0;i<selected.size();++i)
        if(!finite(fc[i])||!finite(fa[i]))throw Refusal("nonfinite-official-face");
    std::map<Label,Foam::point> estimate;std::map<Label,unsigned> count;
    for(Label c:cells)estimate[c]=Foam::point::zero;
    for(std::size_t i=0;i<selected.size();++i)
    {
        const Label f=selected[i];
        for(Label c:{m.owner[f],m.neighbour[f]})if(cells.count(c)){estimate[c]+=fc[i];++count[c];}
    }
    for(Label c:cells)
    {
        if(count[c]<4)throw Refusal("open-cell");estimate[c]/=double(count[c]);
        if(!finite(estimate[c]))throw Refusal("nonfinite-official-estimate");
    }
    std::map<Label,Geometry> out;std::map<Label,Foam::point> weighted;
    for(Label c:cells)weighted[c]=Foam::point::zero;
    for(std::size_t i=0;i<selected.size();++i)
    {
        tick();const Label f=selected[i];
        for(Label c:{m.owner[f],m.neighbour[f]})if(cells.count(c))
        {
            const double sign=m.owner[f]==c?1.0:-1.0;
            const double v3=sign*(fa[i]&(fc[i]-estimate[c]));
            if(!std::isfinite(v3))throw Refusal("nonfinite-official-pyramid");
            out[c].volume+=v3;weighted[c]+=v3*(0.75*fc[i]+0.25*estimate[c]);
            if(!std::isfinite(out[c].volume)||!finite(weighted[c]))
                throw Refusal("nonfinite-official-accumulation");
        }
    }
    for(auto& item:out)
    {
        if(!(item.second.volume>0)||!std::isfinite(item.second.volume))throw Refusal("nonpositive-official-volume");
        item.second.centre=weighted[item.first]/item.second.volume;item.second.volume/=3.0;
        if(!finite(item.second.centre))throw Refusal("nonfinite-official-centre");
        // Restore the public coordinate frame once, after all signed-pyramid
        // accumulation. A null reference preserves the legacy arithmetic.
        if(reference)item.second.centre+=*reference;
        if(!finite(item.second.centre))throw Refusal("nonfinite-official-centre");
    }
    if(out.size()!=cells.size())throw Refusal("missing-official-cell");
    return out;
}
template<class Mesh,class Tick>
void closed(const Mesh& m,const std::set<Label>& cells,Tick tick,
    const std::function<void()>& hostScan=std::function<void()>())
{
    std::map<Label,std::map<Edge,std::pair<unsigned,int>>> edges;
    for(std::size_t f=0;f<m.faces.size();++f)
    {
        if(hostScan)hostScan();else tick();if(!m.faceAlive[f]||(!cells.count(m.owner[f])&&!cells.count(m.neighbour[f])))continue;
        const auto& r=m.faces[f];if(r.size()<3||r.size()>1024)throw Refusal("ring-size");
        std::set<Label> ids(r.begin(),r.end());if(ids.size()!=r.size())throw Refusal("repeated-ring-point");
        if(m.owner[f]<0||m.owner[f]>=m.nCells||m.neighbour[f]>=m.nCells||m.owner[f]==m.neighbour[f])throw Refusal("face-incidence");
        for(Label c:{m.owner[f],m.neighbour[f]})if(cells.count(c))
        {
            const int sign=m.owner[f]==c?1:-1;
            for(std::size_t j=0;j<r.size();++j)
            {Label a=r[j],b=r[(j+1)%r.size()];auto& u=edges[c][std::minmax(a,b)];++u.first;u.second+=sign*(a<b?1:-1);}
        }
    }
    for(Label c:cells)
    {if(edges[c].empty())throw Refusal("empty-cell");for(const auto& e:edges[c])if(e.second.first!=2||e.second.second!=0)throw Refusal("directed-cell-closure");}
}
// The caller owns the original carve clock, patch policy and native piece hash.
// No material decision is inferred from rounded coordinates. The supplied Input
// must be the same actual prepared-shell authority that produced Output.
// A physical source contact can be an original shared face of the next
// partition. Require fresh native facets to cover its actual polygon exactly;
// a patch number or a shared supporting plane alone is never permission.
// A virtual face diagonal is local to one exact simple original polygon.
// It does not authorize propagation as an original mesh edge.
inline bool originalFaceDiagonalAllowed(const D::Input& authority,
    const D::Event& event,const D::ExactPoint& point,std::string* refusal=nullptr)
{
    try
    {
        X::Context work{authority};work.tick();
        if(!authority.leaseHeld||!authority.leaseHeld()||!authority.window.complete
          ||event.kind!=D::Event::Kind::originalFaceDiagonal
          ||event.constructionCell!=authority.cell.id||event.originalFace<0
          ||event.originalEdge[0]<0||event.originalEdge[0]>=event.originalEdge[1]
          ||event.surface!=authority.window.surface||event.shell!=authority.window.shell
          ||event.geometryEpoch!=authority.window.geometryEpoch
          ||event.membershipEpoch!=authority.window.membershipEpoch
          ||event.originalPoint!=-1||event.sourceTriangle!=-1||event.sourceCellFace!=-1
          ||event.sourceEdge!=std::array<D::Id,2>{{-1,-1}})
            throw Refusal("diagonal-event-identity");
        const D::OriginalFace* original=nullptr;
        for(const auto& face:authority.cell.faces)if(face.id==event.originalFace)
        {if(original)throw Refusal("diagonal-duplicate-parent");original=&face;}
        if(!original||original->pointIds.size()!=original->points.size()
          ||original->points.size()<4||original->points.size()>256
          ||(original->owner!=authority.cell.id&&original->neighbour!=authority.cell.id))
            throw Refusal("diagonal-parent");
        X::QFace parent;parent.originalFace=original->id;
        std::map<D::Id,D::ExactPoint> vertices;std::set<D::ExactPoint> coordinates;
        for(std::size_t j=0;j<original->pointIds.size();++j)
        {
            work.tick();if(!X::finite(original->points[j]))throw Refusal("diagonal-nonfinite-parent");
            const auto p=X::originalExactPoint(authority.cell,original->pointIds[j],original->points[j]);
            if(!vertices.emplace(original->pointIds[j],p).second||!coordinates.insert(p).second)
                throw Refusal("diagonal-repeated-parent");
            parent.q.push_back(p);
        }
        if(!vertices.count(event.originalEdge[0])||!vertices.count(event.originalEdge[1]))
            throw Refusal("diagonal-foreign-vertex");
        const auto a=vertices.at(event.originalEdge[0]),b=vertices.at(event.originalEdge[1]);
        if(!X::onSegment(point,a,b))throw Refusal("diagonal-node-off-segment");
        const auto normal=X::area(parent.q);
        if(!X::nonzero(normal))throw Refusal("diagonal-zero-parent");
        for(std::size_t j=0;j<parent.q.size();++j)
        {
            work.tick();const auto x=parent.q[j],y=parent.q[(j+1)%parent.q.size()];
            if(X::side(normal,parent.q.front(),x)!=0)throw Refusal("diagonal-nonplanar-parent");
            if(X::onSegment(a,x,y)&&X::onSegment(b,x,y))throw Refusal("diagonal-is-original-edge");
        }
        if(original->owner!=authority.cell.id)std::reverse(parent.q.begin(),parent.q.end());
        const auto pieces=X::convexOriginalPieces(parent,work);
        unsigned forward=0,backward=0;
        for(const auto& piece:pieces)for(std::size_t j=0;j<piece.q.size();++j)
        {
            work.tick();const auto x=piece.q[j],y=piece.q[(j+1)%piece.q.size()];
            if(x==a&&y==b)++forward;if(x==b&&y==a)++backward;
        }
        if(forward!=1||backward!=1)throw Refusal("diagonal-not-exact-partition-edge");
        work.tick();if(!authority.leaseHeld())throw Refusal("diagonal-stale-authority");
        return true;
    }
    catch(const std::exception& e){if(refusal){try{*refusal=e.what();}catch(...){}}return false;}
    catch(...){if(refusal){try{*refusal="diagonal-unknown-refusal";}catch(...){}}return false;}
}

template<class Mesh>
bool sourceContactFaceAllowed(const Mesh& m,Label f,const D::Input& authority,
    const D::Output& output,const std::set<Label>& knownSolid,std::string* refusal=nullptr)
{
    try
    {
        X::Context work{authority};work.tick();
        if(!authority.leaseHeld||!authority.leaseHeld()||!authority.window.complete
          ||!output.geometricKnown||output.originalCell!=authority.cell.id)
            throw Refusal("contact-stale-authority");
        const Label seed=Label(authority.cell.id);
        if(D::Id(seed)!=authority.cell.id||seed<0||seed>=m.nCells||knownSolid.count(seed)
          ||f<0||std::size_t(f)>=m.faces.size()||std::size_t(f)>=m.faceAlive.size()
          ||std::size_t(f)>=m.owner.size()||std::size_t(f)>=m.neighbour.size()
          ||std::size_t(f)>=m.patch.size()||std::size_t(f)>=m.zone.size()
          ||!m.faceAlive[f]||m.patch[f]<0||m.zone[f]!=-1||m.neighbour[f]<0
          ||(m.owner[f]!=seed&&m.neighbour[f]!=seed))throw Refusal("contact-original-role");
        const Label other=m.owner[f]==seed?m.neighbour[f]:m.owner[f];
        if(other<0||other>=m.nCells||!knownSolid.count(other)
          ||std::size_t(seed)>=m.cellAlive.size()||std::size_t(other)>=m.cellAlive.size()
          ||!m.cellAlive[seed]||!m.cellAlive[other]||std::size_t(seed)>=m.cellRank.size()
          ||std::size_t(other)>=m.cellRank.size()||m.cellRank[seed]<0
          ||m.cellRank[seed]!=m.cellRank[other])throw Refusal("contact-solid-incidence");
        const D::OriginalFace* old=nullptr;
        for(const auto& face:authority.cell.faces)if(face.id==f)
        {if(old)throw Refusal("contact-duplicate-original");old=&face;}
        if(!old||old->owner!=m.owner[f]||old->neighbour!=m.neighbour[f]
          ||old->patch!=m.patch[f]||old->zone!=m.zone[f]||old->pointIds.size()!=m.faces[f].size()
          ||old->points.size()!=old->pointIds.size()||old->points.size()<3
          ||old->points.size()>256)throw Refusal("contact-stale-original");
        X::QFace parent;parent.originalFace=f;
        std::set<D::Id> parentIds;std::set<D::ExactPoint> parentCoordinates;
        for(std::size_t j=0;j<old->points.size();++j)
        {
            work.tick();const Label p=Label(old->pointIds[j]);
            if(p<0||D::Id(p)!=old->pointIds[j]||std::size_t(p)>=m.points.size()
              ||m.faces[f][j]!=p||!X::finite(old->points[j]))throw Refusal("contact-original-point");
            for(unsigned d=0;d<3;++d)if(!sameBits(m.points[p][d],old->points[j][d]))
                throw Refusal("contact-original-bits");
            const auto coordinate=X::originalExactPoint(authority.cell,old->pointIds[j],old->points[j]);
            if(!parentIds.insert(old->pointIds[j]).second||!parentCoordinates.insert(coordinate).second)
                throw Refusal("contact-repeated-original");
            parent.q.push_back(coordinate);
        }
        if(m.owner[f]!=seed)std::reverse(parent.q.begin(),parent.q.end());
        const auto normal=X::area(parent.q);
        if(!X::nonzero(normal))throw Refusal("contact-zero-original");
        for(const auto& p:parent.q)if(X::side(normal,parent.q.front(),p)!=0)
            throw Refusal("contact-nonplanar-original");
        const auto parentPieces=X::convexOriginalPieces(parent,work);
        std::map<D::Id,const D::NativeFacet*> native;
        for(const auto& t:authority.window.facets)
        {
            work.tick();if(!native.emplace(t.triangle,&t).second)
                throw Refusal("contact-duplicate-native");
        }
        std::vector<X::Ring> pieces;X::ExactPoint sum{{D::Rational(0),D::Rational(0),D::Rational(0)}};
        for(const auto& contact:output.contacts)if(contact.originalFace==f)
        {
            work.tick();if(contact.solidOnCurrentCellSide||contact.region!=m.patch[f]
              ||contact.ring.size()<3||contact.ring.size()>256||pieces.size()>=2048)
                throw Refusal("contact-side-or-region");
            const auto found=native.find(contact.sourceTriangle);
            if(found==native.end())throw Refusal("contact-missing-native");
            const auto& t=*found->second;
            if(t.surface!=authority.window.surface||t.shell!=authority.window.shell
              ||t.geometryEpoch!=authority.window.geometryEpoch
              ||t.membershipEpoch!=authority.window.membershipEpoch||t.globalRegion!=contact.region)
                throw Refusal("contact-native-identity");
            X::QFace source;for(const auto& p:t.p)
            {if(!X::finite(p))throw Refusal("contact-nonfinite-native");source.q.push_back(X::exact(p));}
            const auto nativeNormal=X::area(source.q);
            if(!X::nonzero(nativeNormal)||X::dot(normal,nativeNormal)>=0)
                throw Refusal("contact-native-direction");
            X::Ring ring;std::set<std::size_t> nodeIds;std::set<D::ExactPoint> coordinates;
            for(auto id:contact.ring)
            {
                work.tick();if(id>=output.nodes.size())throw Refusal("contact-node-id");
                const auto& node=output.nodes[id];
                if(!X::finite(node.p)||X::rounded(node.exactPoint)!=node.p
                  ||!X::insideFace(node.exactPoint,source))
                    throw Refusal("contact-node-outside-source-or-original");
                if(!nodeIds.insert(id).second||!coordinates.insert(node.exactPoint).second)
                    throw Refusal("contact-repeated-ring");
                ring.push_back(node.exactPoint);
            }
            if(X::dot(X::area(ring),nativeNormal)<=0)throw Refusal("contact-ring-direction");
            std::reverse(ring.begin(),ring.end());
            X::QFace piece;piece.q=ring;
            for(const auto& p:ring)if(!X::insideFace(p,piece))throw Refusal("contact-nonconvex-piece");
            if(X::convexOriginalPieces(piece,work).size()!=1)throw Refusal("contact-nonsimple-piece");
            X::ExactPoint covered{{D::Rational(0),D::Rational(0),D::Rational(0)}};
            for(const auto& parentPiece:parentPieces)
            {
                X::Ring intersection=ring;
                for(std::size_t j=0;j<parentPiece.q.size()&&!intersection.empty();++j)
                    intersection=X::clipRing(intersection,
                        X::cross(X::sub(parentPiece.q[(j+1)%parentPiece.q.size()],parentPiece.q[j]),normal),parentPiece.q[j],work);
                if(intersection.size()>=3)covered=X::add(covered,X::area(intersection));
            }
            if(covered!=X::area(ring))throw Refusal("contact-piece-outside-original");
            for(const auto& previous:pieces)
            {
                X::Ring intersection=ring;
                for(std::size_t j=0;j<previous.size()&&!intersection.empty();++j)
                    intersection=X::clipRing(intersection,
                        X::cross(X::sub(previous[(j+1)%previous.size()],previous[j]),normal),previous[j],work);
                if(intersection.size()>=3&&X::nonzero(X::area(intersection)))
                    throw Refusal("contact-overlapping-coverage");
            }
            sum=X::add(sum,X::area(ring));pieces.push_back(std::move(ring));
        }
        if(pieces.empty()||sum!=normal)throw Refusal("contact-incomplete-coverage");
        work.tick();if(!authority.leaseHeld())throw Refusal("contact-stale-authority");work.tick();
        return true;
    }
    catch(const std::exception& e){if(refusal){try{*refusal=e.what();}catch(...){}}return false;}
    catch(...){if(refusal){try{*refusal="contact-unknown-refusal";}catch(...){}}return false;}
}

// A newly rounded construction node can lie on an actual source edge
// without being an explicit vertex of a source/contact output ring. Unknown
// material may become ON only when BOTH exact and stored points are on the
// same fresh native triangle; convexity then contains the whole rounding
// segment. This grants no wall face, solid cell, or old-point correction.
inline bool unknownNodeSourceContact(const D::Input& authority,const D::Node& node)
{
    try
    {
        X::Context work{authority};work.tick();
        if(!authority.leaseHeld||!authority.leaseHeld()||!authority.window.complete
          ||authority.window.surface<0||authority.window.shell<0
          ||!authority.window.geometryEpoch
          ||authority.window.geometryEpoch!=authority.window.membershipEpoch
          ||authority.window.facets.empty()||authority.window.facets.size()>authority.limits.facets
          ||!X::finite(node.p)||X::rounded(node.exactPoint)!=node.p)return false;
        const auto stored=X::exact(node.p);
        for(const auto& facet:authority.window.facets)
        {
            work.tick();
            if(facet.surface!=authority.window.surface||facet.shell!=authority.window.shell
              ||facet.geometryEpoch!=authority.window.geometryEpoch
              ||facet.membershipEpoch!=authority.window.membershipEpoch
              ||facet.triangle<0||facet.globalRegion<0)continue;
            X::QFace actual;bool finite=true;
            for(const auto& point:facet.p)
            {work.tick();if(!X::finite(point)){finite=false;break;}actual.q.push_back(X::exact(point));}
            if(!finite||!X::nonzero(X::area(actual.q)))continue;
            if(X::insideFace(node.exactPoint,actual)&&X::insideFace(stored,actual))
            {work.tick();if(!authority.leaseHeld())return false;work.tick();return true;}
        }
        return false;
    }
    catch(...){return false;}
}

template<class Mesh,class CompletePointStar,class FaceAllowed,class PieceHash>
bool stage(Mesh& m,const dcCartPreparedFacetContext::FullStar& star,
           const D::Input& authority,const D::Output& output,
           std::vector<signed char>& side,std::set<Label>& knownSolid,
           CompletePointStar completePointStar,FaceAllowed originalFaceAllowed,
           PieceHash pieceHash,std::string* refusal=nullptr,
           std::set<Label>* knownSolidOriginalFaces=nullptr,
           std::vector<unsigned char>* exactPointPins=nullptr,
           std::map<D::Id,D::OriginalExactPoint>* exactConstruction=nullptr,
           const HostLimits* host=nullptr,Mesh* destination=nullptr,
           const std::function<bool(Label)>& originalZoneAllowed={},
           OriginalFaceReceipts* originalReceipts=nullptr,
           ProtectedOriginalFaceDiagnostic* protectedDiagnostic=nullptr)
{
    try
    {
        // A destination is a separate, unpublished caller-owned scratch mesh.
        // Side/metadata outputs must also be scratch-owned. A pins vector inside
        // destination stays at the same subobject address across move assignment.
        if(destination)
        {
            if(destination==&m)throw Refusal("install-destination-alias");
            auto embeddedInBaseline=[&](const void* outputAddress)
            {
                return outputAddress
                    &&std::uintptr_t(outputAddress)-std::uintptr_t(&m)<sizeof(Mesh);
            };
            if(embeddedInBaseline(&side)||embeddedInBaseline(&knownSolid)
               ||embeddedInBaseline(knownSolidOriginalFaces)
               ||embeddedInBaseline(exactPointPins)||embeddedInBaseline(exactConstruction)
               ||embeddedInBaseline(originalReceipts))
                throw Refusal("install-destination-baseline-output-alias");
        }
        std::size_t steps=0,hostSteps=0;
        std::size_t hostBudget=host?hostScanBudget(*host):0;
        auto tick=[&]()
        {if(++steps>2000000||!authority.available||!authority.available()||!dcCartSignedCrossing::supportedFloatingEnvironment())throw Refusal("install-clock-fp-or-cap");};
        auto scanTick=[&]()
        {
            if(!host){tick();return;}
            if(hostSteps>=hostBudget||!authority.available||!authority.available()
               ||!dcCartSignedCrossing::supportedFloatingEnvironment())
                throw Refusal("install-clock-fp-or-cap");
            ++hostSteps;
        };
        auto lease=[&](){tick();if(!authority.leaseHeld||!authority.leaseHeld())throw Refusal("install-source-lease");};
        lease();const Label seed=Label(authority.cell.id);
        const bool multipleSolid=!output.solidParts.empty();
        const std::size_t fluidCount=output.fluidParts.size();
        const std::size_t solidCount=multipleSolid?output.solidParts.size():1;
        if(multipleSolid)
        {
            if(!output.solidPartsKnown||output.solidPartKnown
              ||!output.solidPart.faces.empty()||!output.solidPart.sourceTriangles.empty()
              ||output.solidPart.volume!=0||output.solidPart.kernelMargin!=0
              ||output.solidPart.volumeCentroid!=D::Point{{0,0,0}}
              ||output.solidPart.firstMoment!=D::Point{{0,0,0}}
              ||output.coreMaterial!=D::CoreMaterial::fluid
              ||!authority.materialClassification||!X::finite(output.corePoint)
              ||!(output.coreMargin>0)||!std::isfinite(output.coreMargin))
                throw Refusal("install-multiple-solid-contract");
        }
        else if(!output.solidPartKnown||output.solidPartsKnown
          ||output.coreMaterial!=D::CoreMaterial::solid)
            throw Refusal("install-single-solid-contract");
        if(fluidCount>2048||solidCount>2048||fluidCount+solidCount>2049)
            throw Refusal("install-part-cap");
        auto classify=[&](const D::Point& point)
        {
            lease();
            if(!X::finite(point))throw Refusal("install-material-nonfinite");
            bool solid=false;
            if(authority.materialClassification)
            {
                if(!authority.materialClassification(point,solid))throw Refusal("install-material-unknown");
            }
            else
            {
                if(multipleSolid||!authority.solidMembership)throw Refusal("install-material-callback");
                solid=authority.solidMembership(point);
            }
            lease();return solid;
        };
        if(D::Id(seed)!=authority.cell.id||seed<0||seed>=m.nCells||!m.cellAlive[seed]
          ||!star.complete||star.seed!=seed||!star.cells.count(seed)
          ||output.originalCell!=seed||!output.geometricKnown
          ||output.fluidParts.empty()
          ||output.nodes.size()>8192||!authority.window.complete||!authority.solidMembership)
            throw Refusal("install-incomplete-input");
        if((host?(m.nCells<0||m.faces.size()!=host->faces||m.points.size()!=host->points
              ||std::size_t(m.nCells)!=host->cells)
            :(m.faces.size()>500000||m.points.size()>500000||m.nCells>500000))
          ||side.size()!=m.points.size()||m.preparedPointFootprints.size()!=m.points.size()
          ||m.preparedCompleteCells.size()!=std::size_t(m.nCells))throw Refusal("install-input-shape");
        if(exactPointPins&&!exactPointPins->empty()&&exactPointPins->size()!=m.points.size())
            throw Refusal("install-exact-pin-shape");
        if(m.writerBaseline||m.weightUnionState||m.completeCavityContext||m.completeCavityState
          ||m.completeCavityLineage.started()||!m.coneEvidenceParent.empty()||!m.coneEvidenceBase.empty())
            throw Refusal("install-requires-precone-stage");
        for(std::size_t n:{m.cellAlive.size(),m.cellOrigAlive.size(),m.cellRefVol.size(),m.cellRefCentre.size(),
                           m.cellLevel.size(),m.cellRank.size(),m.cellLeaf.size(),m.cellPiece.size()})
            if(n!=std::size_t(m.nCells))throw Refusal("install-cell-metadata");
        for(std::size_t n:{m.owner.size(),m.neighbour.size(),m.patch.size(),m.zone.size(),m.faceAlive.size()})
            if(n!=m.faces.size())throw Refusal("install-face-metadata");
        std::map<Label,D::OriginalFace> original;std::set<Label> affected;
        for(const auto& c:star.cells)
        {
            tick();if(c.first<0||c.first>=m.nCells||!m.cellAlive[c.first]||!m.preparedCompleteCells[c.first])throw Refusal("incomplete-neighbour");
            affected.insert(Label(c.first));
            for(const auto& f:c.second.faces)
            {
                if(f.id<0||std::size_t(f.id)>=m.faces.size()||!m.faceAlive[f.id]
                  ||m.owner[f.id]!=f.owner||m.neighbour[f.id]!=f.neighbour||m.patch[f.id]!=f.patch||m.zone[f.id]!=f.zone
                  ||m.faces[f.id].size()!=f.pointIds.size()||f.pointIds.size()!=f.points.size())throw Refusal("stale-original-face");
                for(std::size_t j=0;j<f.pointIds.size();++j)
                {
                    if(m.faces[f.id][j]!=f.pointIds[j])throw Refusal("stale-original-ring");
                    for(unsigned d=0;d<3;++d)if(!sameBits(m.points[f.pointIds[j]][d],f.points[j][d]))throw Refusal("stale-original-point");
                }
                original.emplace(Label(f.id),f);
            }
        }
        for(Label p:star.footprintPoints){tick();if(p<0||std::size_t(p)>=m.points.size())throw Refusal("star-point");}
        std::map<Label,D::OriginalFace> seedFaces;std::set<Label> seedPoints;
        std::size_t protectedOrdinal=0;
        for(const auto& f:authority.cell.faces)
        {
            tick();auto it=original.find(Label(f.id));const auto ordinal=protectedOrdinal++;
            auto protectedFailed=[&](bool failed,unsigned condition) noexcept
            {
                if(failed&&protectedDiagnostic&&!protectedDiagnostic->hit)
                {
                    protectedDiagnostic->hit=true;protectedDiagnostic->condition=condition;
                    protectedDiagnostic->ordinal=ordinal;protectedDiagnostic->seed=seed;
                    protectedDiagnostic->face=f.id;protectedDiagnostic->owner=f.owner;
                    protectedDiagnostic->neighbour=f.neighbour;protectedDiagnostic->patch=f.patch;
                    protectedDiagnostic->zone=f.zone;
                }
                return failed;
            };
            if(protectedFailed(it==original.end(),1)
              ||protectedFailed(f.zone>=0&&(!originalReceipts||!originalZoneAllowed||!originalZoneAllowed(Label(f.id))),2)
              ||protectedFailed(!originalFaceAllowed(Label(f.id)),3)
              ||protectedFailed(f.owner!=seed&&f.neighbour!=seed,4)
              ||protectedFailed(!seedFaces.emplace(Label(f.id),f).second,5))
                throw Refusal("protected-original-face");
            if(f.neighbour>=0&&(!affected.count(Label(f.owner))||!affected.count(Label(f.neighbour))))throw Refusal("missing-shared-face-neighbour");
            if(f.pointIds!=it->second.pointIds||f.points!=it->second.points||f.owner!=it->second.owner||f.neighbour!=it->second.neighbour||f.patch!=it->second.patch||f.zone!=it->second.zone)throw Refusal("input-cell-stale");
            for(D::Id p:f.pointIds)seedPoints.insert(Label(p));
        }
        for(const auto& ps:authority.cell.pointSides)if(ps.first<0||std::size_t(ps.first)>=side.size()||side[ps.first]!=ps.second)throw Refusal("stale-input-side");
        if(seedFaces.size()!=star.cells.at(seed).faces.size())throw Refusal("missing-seed-face");
        for(Label p:seedPoints){tick();if(!completePointStar(p))throw Refusal("incomplete-original-point-star");}
        // Exact construction coordinates are retained only by an earlier
        // successful transaction under this same live source. They never move
        // the stored mesh points or authorize a coordinate-only alias.
        auto sameStored=[&](const D::Point& a,const D::Point& b)
        {for(unsigned d=0;d<3;++d)if(!sameBits(a[d],b[d]))return false;return true;};
        auto sameConstruction=[&](const D::OriginalExactPoint& a,const D::OriginalExactPoint& b)
        {return a.exactPoint==b.exactPoint&&sameStored(a.storedPoint,b.storedPoint)
            &&a.surface==b.surface&&a.shell==b.shell&&a.geometryEpoch==b.geometryEpoch
            &&a.membershipEpoch==b.membershipEpoch;};
        if(!exactConstruction&&!authority.cell.exactPoints.empty())
            throw Refusal("install-unbound-original-exact");
        if(exactConstruction)
        {
            if(exactConstruction->size()>m.points.size())throw Refusal("install-construction-cap");
            if(host&&exactConstruction->size()>host->points)throw Refusal("install-host-cache-shape");
            for(const auto& item:*exactConstruction)
            {
                scanTick();const auto id=item.first;const auto& value=item.second;
                if(id<0||std::size_t(id)>=m.points.size()
                  ||value.surface!=authority.window.surface||value.shell!=authority.window.shell
                  ||value.geometryEpoch!=authority.window.geometryEpoch
                  ||value.membershipEpoch!=authority.window.membershipEpoch)
                    throw Refusal("install-stale-construction-identity");
                const D::Point stored{{m.points[id][0],m.points[id][1],m.points[id][2]}};
                if(!X::finite(stored)||!sameStored(stored,value.storedPoint)
                  ||!sameStored(X::rounded(value.exactPoint),stored))
                    throw Refusal("install-stale-construction-point");
                if(seedPoints.count(Label(id)))
                {
                    const auto found=authority.cell.exactPoints.find(id);
                    if(found==authority.cell.exactPoints.end()||!sameConstruction(found->second,value))
                        throw Refusal("install-missing-original-exact");
                }
            }
        }
        for(const auto& item:authority.cell.exactPoints)
        {
            tick();if(!seedPoints.count(Label(item.first))||D::Id(Label(item.first))!=item.first
              ||!exactConstruction||!exactConstruction->count(item.first)
              ||!sameConstruction(exactConstruction->at(item.first),item.second))
                throw Refusal("install-foreign-original-exact");
        }
        if(output.originalFacePlans.size()!=seedFaces.size())throw Refusal("old-face-plan-count");
        std::vector<const D::Part*> solids;
        if(multipleSolid)for(const auto& part:output.solidParts)solids.push_back(&part);
        else solids.push_back(&output.solidPart);
        std::set<std::pair<std::size_t,std::size_t>> fluidPlan,solidPlan;std::set<Label> planIds;
        for(const auto& plan:output.originalFacePlans)
        {
            tick();auto i=seedFaces.find(Label(plan.originalFace));
            if(i==seedFaces.end()||!planIds.insert(Label(plan.originalFace)).second)throw Refusal("unknown-old-face-plan");
            const auto& old=i->second;
            if(plan.owner!=old.owner||plan.neighbour!=old.neighbour||plan.patch!=old.patch||plan.zone!=old.zone)throw Refusal("old-face-plan-metadata");
            for(auto q:plan.fluidPartFaces)
            {
                if(q.first>=output.fluidParts.size()||q.second>=output.fluidParts[q.first].faces.size()
                  ||output.fluidParts[q.first].faces[q.second].originalFace!=plan.originalFace||!fluidPlan.insert(q).second)throw Refusal("fluid-face-plan");
            }
            if(multipleSolid&&!plan.solidPartFaces.empty())throw Refusal("mixed-solid-face-plan");
            if(!multipleSolid&&!plan.solidPartFacePieces.empty())throw Refusal("mixed-solid-face-plan");
            auto solidFace=[&](std::size_t part,std::size_t face)
            {
                if(part>=solids.size()||face>=solids[part]->faces.size()
                  ||solids[part]->faces[face].originalFace!=plan.originalFace
                  ||!solidPlan.insert({part,face}).second)throw Refusal("solid-face-plan");
            };
            for(auto face:plan.solidPartFaces)solidFace(0,face);
            for(auto piece:plan.solidPartFacePieces)solidFace(piece.first,piece.second);
            if(plan.fluidPartFaces.empty()&&plan.solidPartFaces.empty()&&plan.solidPartFacePieces.empty())throw Refusal("empty-old-face-plan");
        }
        // Use one immutable original point as the common numerical frame for
        // both snapshots. The stored mesh and its public coordinates stay unchanged.
        if(authority.cell.faces.empty()||authority.cell.faces.front().pointIds.empty())
            throw Refusal("missing-geometry-reference");
        const auto referenceId=authority.cell.faces.front().pointIds.front();
        if(referenceId<0||std::uint64_t(referenceId)>=m.points.size())
            throw Refusal("invalid-geometry-reference");
        const Foam::point geometryReference=m.points[std::size_t(referenceId)];
        const auto before=geometry(m,affected,tick,&geometryReference,scanTick);closed(m,affected,tick,scanTick);
        Mesh staged=m;auto stagedSide=side;auto stagedSolid=knownSolid;
        auto stagedPins=exactPointPins?*exactPointPins:std::vector<unsigned char>();
        auto stagedConstruction=exactConstruction?*exactConstruction:std::map<D::Id,D::OriginalExactPoint>();
        if(exactPointPins)stagedPins.resize(m.points.size(),0);
        // Typed original-face solid fragments survive the old neighbour clip.
        // Source/contact faces are deliberately excluded from this authority.
        auto stagedSolidOriginal=knownSolidOriginalFaces
            ?*knownSolidOriginalFaces:std::set<Label>();
        auto stagedOriginalReceipts=originalReceipts?*originalReceipts:OriginalFaceReceipts();
        const std::size_t count=fluidCount+solidCount;
        auto isSolidPart=[&](std::size_t part){return part>=fluidCount;};
        std::vector<const D::Part*> parts;for(const auto& p:output.fluidParts)parts.push_back(&p);
        parts.insert(parts.end(),solids.begin(),solids.end());
        std::vector<Label> cellIds(count);cellIds[0]=seed;
        const auto parentPiece=m.cellPiece[seed];std::set<std::uint64_t> childPieces;
        for(std::size_t i=0;i<count;++i)
        {
            tick();if(!(parts[i]->volume>0)||!std::isfinite(parts[i]->volume))throw Refusal("part-volume");
            const auto piece=pieceHash(parentPiece,std::uint64_t(i+1));if(!childPieces.insert(piece).second)throw Refusal("child-piece-collision");
            if(i==0){staged.cellPiece[seed]=piece;continue;}
            if(staged.nCells>=std::numeric_limits<Label>::max())throw Refusal("install-cell-index-overflow");
            const Label c=staged.nCells++;cellIds[i]=c;
            staged.cellAlive.push_back(true);staged.cellOrigAlive.push_back(m.cellOrigAlive[seed]);
            staged.cellRefVol.push_back(m.cellRefVol[seed]);staged.cellRefCentre.push_back(m.cellRefCentre[seed]);
            staged.cellLevel.push_back(m.cellLevel[seed]);staged.cellRank.push_back(m.cellRank[seed]);
            staged.cellLeaf.push_back(m.cellLeaf[seed]);staged.cellPiece.push_back(piece);staged.preparedCompleteCells.push_back(true);
            if(staged.preparedBirth.extractionEpoch>0)
            {
                const char* lineageWhy=nullptr;
                if(!dcCartPreparedFacetBirth::appendCellOrigin(staged,seed,c,&lineageWhy))
                    throw Refusal(lineageWhy?lineageWhy:"birth-cell-lineage-shape");
            }
        }
        std::set<std::size_t> used,onSource;
        std::map<std::size_t,std::set<D::Id>> sourceNodeTriangles;
        for(std::size_t c=0;c<parts.size();++c)for(std::size_t f=0;f<parts[c]->faces.size();++f)
        {
            const auto& face=parts[c]->faces[f];
            if(face.ring.size()<3||face.ring.size()>256||face.zone<-1)throw Refusal("part-face-shape");
            // A zone remains an original internal sheet, never source/curtain authority.
            if(face.originalFace>=0&&seedFaces.count(Label(face.originalFace)))
            {
                const auto& old=seedFaces.at(Label(face.originalFace));
                if(old.zone>=0)
                {
                    if(face.role!=D::Role::original||face.zone!=old.zone
                       ||!originalZoneAllowed||!originalZoneAllowed(Label(old.id)))
                        throw Refusal("zoned-original-role-or-identity");
                    if(isSolidPart(c)&&!knownSolidOriginalFaces)
                        throw Refusal("zoned-solid-requires-typed-handoff");
                }
                else if(face.zone!=-1)throw Refusal("foreign-part-zone");
            }
            else if(face.zone!=-1)throw Refusal("foreign-part-zone");
            if(face.originalFace>=0)
            {
                if(!seedFaces.count(Label(face.originalFace))||(isSolidPart(c)?!solidPlan.count({c-fluidCount,f}):!fluidPlan.count({c,f})))throw Refusal("unplanned-original-fragment");
            }
            else if(face.role==D::Role::original)throw Refusal("missing-original-fragment-id");
            if(face.role==D::Role::source&&(face.region<0||face.sourceTriangle<0))throw Refusal("missing-source-face-identity");
            for(auto n:face.ring){if(n>=output.nodes.size())throw Refusal("node-index");used.insert(n);if(face.role==D::Role::source){onSource.insert(n);sourceNodeTriangles[n].insert(face.sourceTriangle);}}
        }
        // Certify zoned original coverage in current logical exact geometry.
        // A provisional grid zone is not reclassified by a raw-STL plane.
        D::Input coverageAuthority;coverageAuthority.limits=authority.limits;
        coverageAuthority.available=[&]()
        {tick();return authority.leaseHeld&&authority.leaseHeld();};
        X::Context coverageWork{coverageAuthority};
        for(const auto& plan:output.originalFacePlans)
        {
            const auto& old=seedFaces.at(Label(plan.originalFace));
            if(old.zone<0)continue;
            X::QFace parent;
            for(std::size_t j=0;j<old.pointIds.size();++j)
            {coverageWork.tick();parent.q.push_back(X::originalExactPoint(authority.cell,old.pointIds[j],old.points[j]));}
            const auto normal=X::area(parent.q);
            if(!X::nonzero(normal))throw Refusal("zoned-original-zero-area");
            for(const auto& p:parent.q)
            {
                coverageWork.tick();
                if(X::dot(normal,X::sub(p,parent.q.front()))!=0)
                    throw Refusal("zoned-original-nonplanar-parent");
            }
            const auto parentPieces=X::convexOriginalPieces(parent,coverageWork);
            std::vector<X::Ring> coveredPieces;
            D::ExactPoint sum{{D::Rational(0),D::Rational(0),D::Rational(0)}};
            auto certify=[&](const D::Face& face)
            {
                X::Ring ring;
                for(auto node:face.ring)
                {
                    coverageWork.tick();if(node>=output.nodes.size())throw Refusal("zoned-original-node");
                    const auto& p=output.nodes[node].exactPoint;
                    if(X::dot(normal,X::sub(p,parent.q.front()))!=0)
                        throw Refusal("zoned-original-off-plane");
                    ring.push_back(p);
                }
                if(old.owner!=seed)std::reverse(ring.begin(),ring.end());
                if(X::dot(X::area(ring),normal)<=0)throw Refusal("zoned-original-direction");
                X::QFace piece;piece.q=ring;
                if(X::convexOriginalPieces(piece,coverageWork).size()!=1)
                    throw Refusal("zoned-original-nonconvex-piece");
                D::ExactPoint covered{{D::Rational(0),D::Rational(0),D::Rational(0)}};
                for(const auto& ear:parentPieces)
                {
                    X::Ring intersection=ring;
                    for(std::size_t j=0;j<ear.q.size()&&!intersection.empty();++j)
                        intersection=X::clipRing(intersection,
                            X::cross(X::sub(ear.q[(j+1)%ear.q.size()],ear.q[j]),normal),
                            ear.q[j],coverageWork);
                    if(intersection.size()>=3)covered=X::add(covered,X::area(intersection));
                }
                if(covered!=X::area(ring))throw Refusal("zoned-original-outside-parent");
                for(const auto& previous:coveredPieces)
                {
                    X::Ring intersection=ring;
                    for(std::size_t j=0;j<previous.size()&&!intersection.empty();++j)
                        intersection=X::clipRing(intersection,
                            X::cross(X::sub(previous[(j+1)%previous.size()],previous[j]),normal),
                            previous[j],coverageWork);
                    if(intersection.size()>=3&&X::nonzero(X::area(intersection)))
                        throw Refusal("zoned-original-overlap");
                }
                sum=X::add(sum,X::area(ring));coveredPieces.push_back(std::move(ring));
            };
            for(auto q:plan.fluidPartFaces)certify(output.fluidParts[q.first].faces[q.second]);
            for(auto f:plan.solidPartFaces)certify(solids[0]->faces[f]);
            for(auto q:plan.solidPartFacePieces)certify(solids[q.first]->faces[q.second]);
            if(coveredPieces.empty()||sum!=normal)throw Refusal("zoned-original-incomplete-cover");
        }
        // Exact source incidence grants ON, including contact-only nodes. A
        // failed winding query never grants fluid. Validate the actual native
        // facet and original polygon before consuming contact nodes or an
        // explicit correction of a legacy original-point side cache.
        std::map<D::Id,const D::NativeFacet*> native;
        for(const auto& facet:authority.window.facets)
        {tick();if(!native.emplace(facet.triangle,&facet).second)throw Refusal("install-duplicate-native-facet");}
        auto nativeFace=[&](D::Id triangle,D::Id region)
        {
            tick();const auto it=native.find(triangle);
            if(it==native.end())throw Refusal("install-source-facet-missing");
            const auto& facet=*it->second;
            if(facet.surface!=authority.window.surface||facet.shell!=authority.window.shell
              ||facet.geometryEpoch!=authority.window.geometryEpoch
              ||facet.membershipEpoch!=authority.window.membershipEpoch
              ||facet.globalRegion<0||(region>=0&&facet.globalRegion!=region))
                throw Refusal("install-source-facet-identity");
            X::QFace face;for(const auto& point:facet.p)
            {if(!X::finite(point))throw Refusal("install-source-facet-nonfinite");face.q.push_back(X::exact(point));}
            if(!X::nonzero(X::area(face.q)))throw Refusal("install-source-facet-zero");
            return face;
        };
        // Only edges of an actual, validated physical SOURCE piece may grant
        // exact ON membership before conform() inserts an already-used node.
        // CONTACT-only edge expansion needs a separate original-parent proof.
        struct CertifiedSourceEdge
        {std::size_t first,second;D::Id triangle;};
        std::vector<CertifiedSourceEdge> certifiedSourceEdges;
        std::set<std::tuple<D::Id,std::size_t,std::size_t>> certifiedSourceEdgeKeys;
        for(const auto& part:parts)for(const auto& face:part->faces)if(face.role==D::Role::source)
        {
            const auto actual=nativeFace(face.sourceTriangle,face.region);
            X::Ring piece;
            for(auto node:face.ring)
            {
                tick();const auto& point=output.nodes.at(node).exactPoint;
                if(!X::insideFace(point,actual))throw Refusal("install-source-node-outside-facet");
                piece.push_back(point);
            }
            if(!X::nonzero(X::area(piece)))throw Refusal("install-source-edge-zero-piece");
            for(std::size_t j=0;j<face.ring.size();++j)
            {
                tick();const auto a=face.ring[j],b=face.ring[(j+1)%face.ring.size()];
                if(output.nodes.at(a).exactPoint==output.nodes.at(b).exactPoint)continue;
                const auto key=std::make_tuple(face.sourceTriangle,std::min(a,b),std::max(a,b));
                if(certifiedSourceEdgeKeys.insert(key).second)
                    certifiedSourceEdges.push_back({a,b,face.sourceTriangle});
            }
        }
        X::Context contactWork{authority};
        for(const auto& contact:output.contacts)
        {
            tick();const auto old=seedFaces.find(Label(contact.originalFace));
            if(D::Id(Label(contact.originalFace))!=contact.originalFace||old==seedFaces.end()
              ||contact.region<0||contact.ring.size()<3||contact.ring.size()>256)
                throw Refusal("install-contact-parent-or-ring");
            const auto actual=nativeFace(contact.sourceTriangle,contact.region);
            const auto sourceNormal=X::area(actual.q);
            X::QFace parent;for(std::size_t j=0;j<old->second.points.size();++j)
                parent.q.push_back(X::originalExactPoint(authority.cell,old->second.pointIds[j],old->second.points[j]));
            if(old->second.owner!=seed)std::reverse(parent.q.begin(),parent.q.end());
            const auto normal=X::area(parent.q);
            if(!X::nonzero(normal)||X::nonzero(X::cross(normal,sourceNormal))
              ||X::dot(normal,sourceNormal)==0
              ||contact.solidOnCurrentCellSide!=(X::dot(normal,sourceNormal)>0))
                throw Refusal("install-contact-native-direction");
            const auto parentPieces=X::convexOriginalPieces(parent,contactWork);
            X::QFace piece;std::set<std::size_t> ids;std::set<D::ExactPoint> coordinates;
            for(auto node:contact.ring)
            {
                tick();if(node>=output.nodes.size()||!ids.insert(node).second)throw Refusal("install-contact-node");
                const auto& value=output.nodes[node];
                if(!X::finite(value.p)||X::rounded(value.exactPoint)!=value.p
                  ||!X::insideFace(value.exactPoint,actual)
                  ||X::side(normal,parent.q.front(),value.exactPoint)!=0
                  ||!coordinates.insert(value.exactPoint).second)throw Refusal("install-contact-coordinate");
                piece.q.push_back(value.exactPoint);
            }
            if(X::dot(X::area(piece.q),sourceNormal)<=0)throw Refusal("install-contact-ring-direction");
            if(!contact.solidOnCurrentCellSide)std::reverse(piece.q.begin(),piece.q.end());
            for(const auto& point:piece.q)if(!X::insideFace(point,piece))throw Refusal("install-contact-nonconvex");
            if(X::convexOriginalPieces(piece,contactWork).size()!=1)throw Refusal("install-contact-nonsimple");
            D::ExactPoint covered{{D::Rational(0),D::Rational(0),D::Rational(0)}};
            for(const auto& originalPiece:parentPieces)
            {
                X::Ring intersection=piece.q;
                for(std::size_t j=0;j<originalPiece.q.size()&&!intersection.empty();++j)
                    intersection=X::clipRing(intersection,X::cross(X::sub(originalPiece.q[(j+1)%originalPiece.q.size()],originalPiece.q[j]),normal),originalPiece.q[j],contactWork);
                if(intersection.size()>=3)covered=X::add(covered,X::area(intersection));
            }
            if(covered!=X::area(piece.q))throw Refusal("install-contact-outside-parent");
            for(auto node:contact.ring)if(used.count(node))
            {onSource.insert(node);sourceNodeTriangles[node].insert(contact.sourceTriangle);}
        }
        std::map<Label,const D::OriginalPointContact*> pointContacts;
        for(const auto& contact:output.originalPointContacts)
        {
            tick();const Label point=Label(contact.originalPoint);
            if(D::Id(point)!=contact.originalPoint||!seedPoints.count(point)
              ||(contact.previousSide!=-1&&contact.previousSide!=1)
              ||side.at(point)!=contact.previousSide
              ||!authority.cell.pointSides.count(contact.originalPoint)
              ||authority.cell.pointSides.at(contact.originalPoint)!=contact.previousSide
              ||contact.surface!=authority.window.surface||contact.shell!=authority.window.shell
              ||contact.geometryEpoch!=authority.window.geometryEpoch
              ||contact.membershipEpoch!=authority.window.membershipEpoch
              ||!pointContacts.emplace(point,&contact).second)
                throw Refusal("install-original-contact-identity");
            const auto actual=nativeFace(contact.sourceTriangle,-1);
            D::Point pointValue{{m.points[point][0],m.points[point][1],m.points[point][2]}};
            if(!X::insideFace(X::originalExactPoint(authority.cell,point,pointValue),actual))throw Refusal("install-original-contact-outside-source");
        }
        std::set<Label> consumedPointContacts;
        std::map<Label,D::ExactPoint> exact;for(Label p:seedPoints)
        {D::Point x{{m.points[p][0],m.points[p][1],m.points[p][2]}};exact.emplace(p,X::originalExactPoint(authority.cell,p,x));}
        std::vector<Label> nodeIds(output.nodes.size(),-1);std::map<D::Point,Label> roundedNodes;
        auto footprint=[&](const std::set<Label>& ancestors,unsigned char* reason=nullptr)
        {
            if(reason)*reason=ancestors.empty()?1:0;
            std::set<Label> result;for(Label p:ancestors)
            {
                if(p<0||std::size_t(p)>=m.preparedPointFootprints.size()||m.preparedPointFootprints[p].empty())
                {if(reason)*reason=2;return std::vector<Label>();}
                result.insert(m.preparedPointFootprints[p].begin(),m.preparedPointFootprints[p].end());if(result.size()>64)
                {if(reason)*reason=3;return std::vector<Label>();}
            }
            return std::vector<Label>(result.begin(),result.end());
        };
        auto extendSourceEdgeMembership=[&](std::size_t ni)
        {
            if(onSource.count(ni))return;
            lease();const auto& point=output.nodes[ni].exactPoint;
            for(const auto& edge:certifiedSourceEdges)
            {
                tick();const auto& a=output.nodes[edge.first].exactPoint;
                const auto& b=output.nodes[edge.second].exactPoint;
                bool within=true;
                for(unsigned d=0;d<3;++d)
                    if(point[d]<std::min(a[d],b[d])||point[d]>std::max(a[d],b[d]))
                    {within=false;break;}
                if(within&&X::onSegment(point,a,b))
                {onSource.insert(ni);sourceNodeTriangles[ni].insert(edge.triangle);}
            }
            lease();
        };
        std::set<std::tuple<D::Id,D::Id,D::Id>> checkedDiagonals;
        for(auto ni:used)
        {
            tick();const auto& node=output.nodes[ni];if(!X::finite(node.p)||node.events.empty())throw Refusal("unbound-output-node");
            const auto rounded=X::rounded(node.exactPoint);
            for(unsigned d=0;d<3;++d)if(rounded[d]!=node.p[d])throw Refusal("node-rounding");
            Label old=-1;std::set<Label> ancestry;
            for(const auto& e:node.events)
            {
                const int kind=int(e.kind);if(kind<0||kind>int(D::Event::Kind::originalFaceDiagonal))throw Refusal("unknown-node-event");
                if(e.kind==D::Event::Kind::originalPoint)
                {
                    if(e.originalPoint<0||!seedPoints.count(Label(e.originalPoint))||(old>=0&&old!=e.originalPoint))throw Refusal("original-node-event");
                    old=Label(e.originalPoint);ancestry.insert(old);
                    if(exact.at(old)!=node.exactPoint)throw Refusal("original-node-coordinate");
                    for(unsigned d=0;d<3;++d)if(!sameBits(m.points[old][d],node.p[d]))throw Refusal("original-node-bits");
                }
                else
                {
                    if(e.surface!=authority.window.surface||e.shell!=authority.window.shell
                      ||e.geometryEpoch!=authority.window.geometryEpoch||e.membershipEpoch!=authority.window.membershipEpoch)throw Refusal("node-source-epoch");
                    if(e.kind==D::Event::Kind::originalFaceDiagonal)
                    {
                        const auto key=std::make_tuple(e.originalFace,e.originalEdge[0],e.originalEdge[1]);
                        if(!checkedDiagonals.count(key))
                        {
                            std::string why;if(!originalFaceDiagonalAllowed(authority,e,node.exactPoint,&why))throw Refusal(why.c_str());
                            checkedDiagonals.insert(key);
                        }
                        else if(e.constructionCell!=authority.cell.id||e.originalPoint!=-1||e.sourceTriangle!=-1
                          ||e.sourceCellFace!=-1||e.sourceEdge!=std::array<D::Id,2>{{-1,-1}}
                          ||!X::onSegment(node.exactPoint,exact.at(Label(e.originalEdge[0])),exact.at(Label(e.originalEdge[1]))))
                            throw Refusal("diagonal-node-or-identity");
                    }
                    for(D::Id a:e.originalEdge)if(a>=0){if(!seedPoints.count(Label(a)))throw Refusal("node-original-edge");ancestry.insert(Label(a));}
                    for(D::Id fid:{e.originalFace,e.sourceCellFace})if(fid>=0)
                    {auto f=seedFaces.find(Label(fid));if(f==seedFaces.end())throw Refusal("node-original-face");for(auto a:f->second.pointIds)ancestry.insert(Label(a));}
                }
            }
            // Events, exact/stored rounding and original-node identity have
            // passed above. Reused old points still need the certificate below.
            extendSourceEdgeMembership(ni);
            if(old>=0&&onSource.count(ni)&&side[old]!=0)
            {
                const auto contact=pointContacts.find(old);
                if(contact==pointContacts.end()||!sourceNodeTriangles.at(ni).count(contact->second->sourceTriangle))
                    throw Refusal("install-unreconciled-original-source-side");
                stagedSide[old]=0;consumedPointContacts.insert(old);
            }
            Label id=old;
            if(id<0)
            {
                if(staged.points.size()>=std::size_t(std::numeric_limits<Label>::max()))throw Refusal("install-point-index-overflow");
                id=Label(staged.points.size());using PointType=typename std::decay<decltype(staged.points)>::type::value_type;
                staged.points.push_back(PointType(node.p[0],node.p[1],node.p[2]));
                signed char pointSide=0;
                if(!onSource.count(ni))
                {
                    if(authority.materialClassification)
                    {
                        lease();bool isSolid=false;
                        const bool known=authority.materialClassification(node.p,isSolid);lease();
                        if(known)pointSide=isSolid?-1:1;
                        else if(!unknownNodeSourceContact(authority,node))throw Refusal("install-material-unknown");
                    }
                    else pointSide=classify(node.p)?-1:1;
                }
                stagedSide.push_back(pointSide);
                unsigned char flatReason=0;
                staged.preparedPointFootprints.push_back(footprint(ancestry,&flatReason));
                if(staged.preparedBirth.extractionEpoch>0)staged.preparedBirth.flatFootprintReasons.emplace(id,flatReason);
                exact.emplace(id,node.exactPoint);
            }
            if(!roundedNodes.emplace(node.p,id).second&&roundedNodes.at(node.p)!=id)throw Refusal("rounded-node-collision");
            nodeIds[ni]=id;
        }
        if(consumedPointContacts.size()!=pointContacts.size())throw Refusal("install-unused-original-contact");
        // The exact arrangement may carry hanging edge subdivisions in its
        // solid complement. Refine with EXISTING, typed nodes of this one
        // certified seed partition; never mint coordinates or weld nearby IDs.
        std::array<std::vector<std::pair<double,Label>>,3> axisNodes;
        for(auto n:used)for(unsigned d=0;d<3;++d)axisNodes[d].push_back({output.nodes[n].p[d],nodeIds[n]});
        for(auto& a:axisNodes)std::sort(a.begin(),a.end());
        std::map<Edge,std::vector<std::pair<D::Rational,Label>>> conformCache;
        auto conform=[&](const Ring& input)
        {
            Ring result;
            for(std::size_t j=0;j<input.size();++j)
            {
                tick();const Label a=input[j],b=input[(j+1)%input.size()];result.push_back(a);
                const Edge edge=std::minmax(a,b);auto cache=conformCache.find(edge);
                if(cache==conformCache.end())
                {
                    std::size_t lower=0,upper=0,best=std::numeric_limits<std::size_t>::max();unsigned axis=0;
                    for(unsigned d=0;d<3;++d)
                    {
                        double lo=std::min(staged.points[a][d],staged.points[b][d]),hi=std::max(staged.points[a][d],staged.points[b][d]);
                        const auto& sorted=axisNodes[d];
                        const auto l=std::lower_bound(sorted.begin(),sorted.end(),std::make_pair(lo,std::numeric_limits<Label>::min()));
                        const auto u=std::upper_bound(sorted.begin(),sorted.end(),std::make_pair(hi,std::numeric_limits<Label>::max()));
                        if(std::size_t(u-l)<best){best=std::size_t(u-l);axis=d;lower=std::size_t(l-sorted.begin());upper=std::size_t(u-sorted.begin());}
                    }
                    std::vector<std::pair<D::Rational,Label>> inner;
                    for(std::size_t k=lower;k<upper;++k)
                    {
                        tick();const Label p=axisNodes[axis][k].second;if(p==a||p==b)continue;
                        bool box=true;for(unsigned d=0;d<3;++d)
                            if(staged.points[p][d]<std::min(staged.points[a][d],staged.points[b][d])
                              ||staged.points[p][d]>std::max(staged.points[a][d],staged.points[b][d]))box=false;
                        if(!box)continue;D::Rational t;
                        if(onSegment(exact.at(p),exact.at(edge.first),exact.at(edge.second),t))inner.push_back({t,p});
                    }
                    std::sort(inner.begin(),inner.end());
                    for(std::size_t k=1;k<inner.size();++k)if(inner[k].first==inner[k-1].first&&inner[k].second!=inner[k-1].second)throw Refusal("partition-exact-node-alias");
                    cache=conformCache.emplace(edge,std::move(inner)).first;
                }
                if(a<b)for(const auto& p:cache->second)result.push_back(p.second);
                else for(auto p=cache->second.rbegin();p!=cache->second.rend();++p)result.push_back(p->second);
                if(result.size()>1024)throw Refusal("partition-edge-refinement-cap");
            }
            return result;
        };
        struct Occurrence{std::size_t part;const D::Face* face;Ring ring;};
        std::map<Ring,std::vector<Occurrence>> uses;
        for(std::size_t c=0;c<parts.size();++c)for(const auto& f:parts[c]->faces)
        {
            Ring r;for(auto n:f.ring)r.push_back(nodeIds[n]);r=conform(r);
            if(std::set<Label>(r.begin(),r.end()).size()!=r.size())throw Refusal("collapsed-fragment");
            uses[key(r)].push_back({c,&f,std::move(r)});
        }
        std::map<Label,std::vector<Label>> replacements;
        auto append=[&](Ring ring,Label own,Label nei,Label patch,Label zone)
        {
            tick();if(own<0||own>=staged.nCells||nei>=staged.nCells||own==nei)throw Refusal("new-face-incidence");
            if(staged.faces.size()>=std::size_t(std::numeric_limits<Label>::max()))
                throw Refusal("install-face-index-overflow");
            if(host)
            {
                if(hostBudget>std::numeric_limits<std::size_t>::max()-3)
                    throw Refusal("install-host-scan-overflow");
                hostBudget+=3;
            }
            const Label id=Label(staged.faces.size());staged.faces.push_back(std::move(ring));staged.owner.push_back(own);staged.neighbour.push_back(nei);
            staged.patch.push_back(patch);staged.zone.push_back(zone);staged.faceAlive.push_back(true);return id;
        };
        auto bindOriginalReceipt=[&](Label f,const D::OriginalFace& old,D::Role role)
        {
            if(!originalReceipts||role!=D::Role::original)return;
            if(!stagedOriginalReceipts.emplace(f,OriginalFaceReceipt{Label(old.id),Label(old.patch),Label(old.zone)}).second)
                throw Refusal("duplicate-original-face-receipt");
        };
        for(const auto& entry:uses)
        {
            tick();const auto& u=entry.second;
            if(u.size()==2)
            {
                const auto& a=u[0];const auto& b=u[1];
                if(a.part==b.part||cycle(a.ring)!=cycle(reverse(b.ring))||a.face->originalFace>=0||b.face->originalFace>=0)throw Refusal("internal-fragment-pair");
                Label patch=-1;
                if(a.face->role==D::Role::source||b.face->role==D::Role::source)
                {
                    if(a.face->role!=b.face->role||a.face->sourceTriangle!=b.face->sourceTriangle||a.face->region!=b.face->region
                      ||isSolidPart(a.part)==isSolidPart(b.part))throw Refusal("source-body-pair");
                    patch=Label(a.face->region);
                }
                else if(a.face->role!=D::Role::curtain||b.face->role!=D::Role::curtain
                  ||isSolidPart(a.part)!=isSolidPart(b.part))throw Refusal("curtain-pair");
                append(a.ring,cellIds[a.part],cellIds[b.part],patch,-1);
            }
            else
            {
                if(u.size()!=1||u[0].face->originalFace<0)throw Refusal("unpaired-nonoriginal-face");
                const auto& a=u[0];const auto& old=seedFaces.at(Label(a.face->originalFace));
                const Label child=cellIds[a.part],own=old.owner==seed?child:Label(old.owner),nei=old.neighbour==seed?child:Label(old.neighbour);
                Ring r=old.owner==seed?a.ring:reverse(a.ring);
                Label patch=Label(old.patch);if(a.face->role==D::Role::source)patch=Label(a.face->region);
                const bool sample=isSolidPart(a.part)&&a.face->role==D::Role::original;
                if(sample)
                {
                    D::ExactPoint centre;for(Label p:r)centre=X::add(centre,exact.at(p));centre=X::mul(centre,D::Rational(1)/D::Integer(r.size()));
                    const auto rounded=X::rounded(centre);if(!classify(rounded))throw Refusal("solid-old-face-sample");
                    if(knownSolidOriginalFaces)
                    {
                        // The following clip consumes the complete typed solid
                        // original face, independently of point-side votes.
                        // Keep the material witness above, without repeatedly
                        // introducing rounded fan centres into that same face.
                        const Label f=append(std::move(r),own,nei,patch,Label(old.zone));
                        replacements[Label(old.id)].push_back(f);
                        bindOriginalReceipt(f,old,a.face->role);
                        stagedSolidOriginal.insert(f);
                    }
                    else
                    {
                        if(staged.points.size()>=std::size_t(std::numeric_limits<Label>::max()))throw Refusal("install-point-index-overflow");
                        const Label mid=Label(staged.points.size());using PointType=typename std::decay<decltype(staged.points)>::type::value_type;
                        staged.points.push_back(PointType(rounded[0],rounded[1],rounded[2]));stagedSide.push_back(-1);exact.emplace(mid,std::move(centre));
                        std::set<Label> ancestors;for(auto p:old.pointIds)ancestors.insert(Label(p));unsigned char flatReason=0;
                        staged.preparedPointFootprints.push_back(footprint(ancestors,&flatReason));
                        if(staged.preparedBirth.extractionEpoch>0)staged.preparedBirth.flatFootprintReasons.emplace(mid,flatReason);
                        for(std::size_t j=0;j<r.size();++j)
                        {
                            const Label f=append({r[j],r[(j+1)%r.size()],mid},own,nei,patch,Label(old.zone));
                            replacements[Label(old.id)].push_back(f);
                            bindOriginalReceipt(f,old,a.face->role);
                        }
                    }
                }
                else
                {
                    const Label f=append(std::move(r),own,nei,patch,Label(old.zone));
                    replacements[Label(old.id)].push_back(f);
                    bindOriginalReceipt(f,old,a.face->role);
                }
            }
        }
        for(const auto& old:seedFaces)
        {if(replacements[old.first].empty())throw Refusal("uncovered-old-face");staged.faceAlive[old.first]=false;}
        // Refine genuine original-ID edges through their complete native star.
        std::map<Edge,std::vector<std::pair<D::Rational,Label>>> edgeNodes;
        for(const auto& item:seedFaces)
        {
            const auto& r=item.second.pointIds;
            for(std::size_t j=0;j<r.size();++j)
            {
                Edge edge=std::minmax(Label(r[j]),Label(r[(j+1)%r.size()]));if(edgeNodes.count(edge))continue;
                auto& found=edgeNodes[edge];
                const auto& a=exact.at(edge.first);const auto& b=exact.at(edge.second);
                D::ExactPoint lower,upper;
                for(unsigned d=0;d<3;++d)
                {lower[d]=std::min(a[d],b[d]);upper[d]=std::max(a[d],b[d]);}
                for(const auto& point:exact)if(point.first>=Label(m.points.size()))
                {
                    tick();bool inBox=true;
                    for(unsigned d=0;d<3;++d)
                        if(point.second[d]<lower[d]||point.second[d]>upper[d])
                        {inBox=false;break;}
                    // Exact closed coordinate bounds are necessary for the
                    // original strict interior segment predicate. No rounded
                    // point, tolerance, source role or material is inferred.
                    if(!inBox)continue;
                    D::Rational t;
                    if(onSegment(point.second,a,b,t))found.push_back({t,point.first});
                }
                std::sort(found.begin(),found.end());
                for(std::size_t k=1;k<found.size();++k)if(found[k].first==found[k-1].first&&found[k].second!=found[k-1].second)throw Refusal("duplicate-edge-node");
            }
        }
        for(std::size_t f=0;f<staged.faces.size();++f)
        {
            scanTick();if(!staged.faceAlive[f])continue;
            Ring expanded;const Ring& r=staged.faces[f];
            for(std::size_t j=0;j<r.size();++j)
            {
                Label a=r[j],b=r[(j+1)%r.size()];expanded.push_back(a);auto found=edgeNodes.find(std::minmax(a,b));
                if(found==edgeNodes.end()||found->second.empty())continue;
                if(f<m.faces.size()&&(!original.count(Label(f))||!originalFaceAllowed(Label(f))))throw Refusal("edge-point-outside-or-protected-star");
                if(a<b)for(const auto& q:found->second)expanded.push_back(q.second);
                else for(auto q=found->second.rbegin();q!=found->second.rend();++q)expanded.push_back(q->second);
            }
            if(expanded.size()>1024)throw Refusal("expanded-ring-cap");
            staged.faces[f]=std::move(expanded);
        }
        // Original face area/centroid checks use the same actual library routine.
        for(const auto& item:seedFaces)
        {
            tick();const Ring& oldRing=m.faces[item.first];
            // Compare both sides of this partition on the SAME conforming
            // stored boundary. An exact edge point rounds independently of its
            // endpoints, so the old unsplit floating segment is not the same
            // discrete boundary as its newly refined floating polyline.
            // Preserve every old corner and direction; use only the two exact
            // edge-refinement caches already consumed by the staged mesh.
            (void)conform(oldRing);
            Ring referenceRing;
            for(std::size_t j=0;j<oldRing.size();++j)
            {
                tick();const Label a=oldRing[j],b=oldRing[(j+1)%oldRing.size()];
                referenceRing.push_back(a);const Edge edge=std::minmax(a,b);
                std::map<D::Rational,Label> ordered;
                auto include=[&](const std::vector<std::pair<D::Rational,Label>>& entries)
                {
                    for(const auto& entry:entries)
                    {
                        tick();const Label id=entry.second;
                        if(!(entry.first>0&&entry.first<1)||id<0
                          ||std::size_t(id)>=staged.points.size()||id==a||id==b)
                            throw Refusal("original-face-refinement-entry");
                        const auto rounded=X::rounded(exact.at(id));
                        for(unsigned d=0;d<3;++d)
                            if(!std::isfinite(staged.points[id][d])||rounded[d]!=staged.points[id][d])
                                throw Refusal("original-face-refinement-rounding");
                        const auto inserted=ordered.emplace(entry.first,id);
                        if(!inserted.second&&inserted.first->second!=id)
                            throw Refusal("original-face-refinement-alias");
                    }
                };
                include(conformCache.at(edge));
                const auto propagated=edgeNodes.find(edge);
                if(propagated!=edgeNodes.end())include(propagated->second);
                if(a<b)for(const auto& q:ordered)referenceRing.push_back(q.second);
                else for(auto q=ordered.rbegin();q!=ordered.rend();++q)referenceRing.push_back(q->second);
                if(referenceRing.size()>1024)throw Refusal("original-face-refinement-cap");
            }
            if(std::set<Label>(referenceRing.begin(),referenceRing.end()).size()!=referenceRing.size())
                throw Refusal("original-face-refinement-duplicate");
            std::vector<Ring> rings{std::move(referenceRing)};
            for(Label f:replacements.at(item.first))rings.push_back(staged.faces[f]);
            // A missing or extra boundary atom cannot be excused by a small
            // metric error. Internal replacement edges cancel in opposite pairs.
            std::map<Edge,std::int64_t> boundary;
            for(std::size_t i=0;i<rings.size();++i)
                for(std::size_t j=0;j<rings[i].size();++j)
                {
                    tick();const Label a=rings[i][j],b=rings[i][(j+1)%rings[i].size()];
                    if(a==b)throw Refusal("original-face-refinement-zero-edge");
                    boundary[std::minmax(a,b)]+=(a<b?1:-1)*(i==0?1:-1);
                }
            for(const auto& edge:boundary)if(edge.second!=0)
                throw Refusal("original-face-refined-boundary");
            // A concave polygon's vertex-average fan can leave its kernel
            // when collinear boundary points are inserted. Use existing exact
            // convex pieces as the temporary metric reference in that case;
            // all vertices remain the same certified stored mesh vertices.
            X::QFace referenceFace;std::map<D::ExactPoint,Label> referenceIds;
            for(Label id:rings[0])
            {
                tick();referenceFace.q.push_back(exact.at(id));
                if(!referenceIds.emplace(exact.at(id),id).second)
                    throw Refusal("original-face-reference-coordinate-alias");
            }
            const auto referenceNormal=X::area(referenceFace.q);bool convexReference=true;
            if(!X::nonzero(referenceNormal))throw Refusal("original-face-reference-zero");
            for(std::size_t j=0;j<referenceFace.q.size();++j)
            {
                tick();const auto& q=referenceFace.q;
                if(X::dot(X::cross(X::sub(q[(j+1)%q.size()],q[j]),
                    X::sub(q[(j+2)%q.size()],q[(j+1)%q.size()])),referenceNormal)<0)
                    {convexReference=false;break;}
            }
            std::size_t referenceCount=1;
            if(!convexReference)
            {
                const auto pieces=X::convexOriginalPieces(referenceFace,contactWork);
                std::vector<Ring> metricRings;metricRings.reserve(pieces.size()+rings.size()-1);
                for(const auto& piece:pieces)
                {
                    tick();Ring r;for(const auto& point:piece.q)r.push_back(referenceIds.at(point));
                    metricRings.push_back(std::move(r));
                }
                referenceCount=metricRings.size();
                for(std::size_t i=1;i<rings.size();++i)metricRings.push_back(std::move(rings[i]));
                rings.swap(metricRings);
            }
            std::map<Label,Label> ids;for(const auto& r:rings)for(Label p:r)if(!ids.count(p))ids[p]=Label(ids.size());
            // Compare this original face and all its replacements in one local
            // frame. Global face-centre rounding can dominate a tiny face's
            // first moment even when every point shares the same exact plane.
            // Only the temporary check buffer moves; the mesh remains unchanged.
            const Foam::point reference=staged.points[oldRing.front()];
            Foam::pointField pp(ids.size());
            for(const auto& p:ids)
            {
                pp[p.second]=staged.points[p.first]-reference;
                for(unsigned d=0;d<3;++d)if(!std::isfinite(pp[p.second][d]))
                    throw Refusal("original-face-nonfinite-local");
            }
            Foam::faceList ff(rings.size());
            for(std::size_t i=0;i<rings.size();++i){ff[i].resize(rings[i].size());for(std::size_t j=0;j<rings[i].size();++j)ff[i][j]=ids.at(rings[i][j]);}
            Foam::vectorField fc,fa;Foam::primitiveMeshTools::makeFaceCentresAndAreas(ff,pp,fc,fa);
            for(Foam::label i=0;i<ff.size();++i)for(unsigned d=0;d<3;++d)
                if(!std::isfinite(fc[i][d])||!std::isfinite(fa[i][d]))
                    throw Refusal("original-face-nonfinite-local");
            Foam::vector referenceVectorArea=fa[0],referenceCentre=fc[0];
            if(referenceCount>1)
            {
                referenceVectorArea=Foam::vector::zero;
                Foam::vector weightedCentre=Foam::vector::zero;double referenceAbsArea=0;
                for(std::size_t i=0;i<referenceCount;++i)
                {
                    tick();const double a=Foam::mag(fa[i]);
                    referenceVectorArea+=fa[i];referenceAbsArea+=a;weightedCentre+=a*fc[i];
                }
                if(!(referenceAbsArea>0)||!std::isfinite(referenceAbsArea))
                    throw Refusal("original-face-reference-zero");
                for(std::size_t i=0;i<referenceCount;++i)
                    if(!(Foam::mag(fa[i])>0)||!((fa[i]&referenceVectorArea)>0))
                        throw Refusal("original-face-reference-orientation");
                referenceCentre=weightedCentre/referenceAbsArea;
                if(std::abs(referenceAbsArea-Foam::mag(referenceVectorArea))>1e-10*referenceAbsArea)
                    throw Refusal("original-face-reference-nonplanar");
            }
            for(unsigned d=0;d<3;++d)
                if(!std::isfinite(referenceVectorArea[d])||!std::isfinite(referenceCentre[d]))
                    throw Refusal("original-face-nonfinite-local");
            Foam::vector area=Foam::vector::zero,moment=Foam::vector::zero;double absArea=0,scale=0;
            for(std::size_t i=0;i<referenceCount;++i)for(Label p:rings[i])
                scale=std::max(scale,Foam::mag(pp[ids.at(p)]-referenceCentre));
            const double referenceArea=Foam::mag(referenceVectorArea);if(!(referenceArea>0&&scale>0)||!std::isfinite(referenceArea)||!std::isfinite(scale))throw Refusal("original-face-zero");
            for(Foam::label i=Foam::label(referenceCount);i<ff.size();++i){area+=fa[i];double a=Foam::mag(fa[i]);absArea+=a;moment+=a*(fc[i]-referenceCentre);}
            if(!std::isfinite(absArea))throw Refusal("original-face-nonfinite-local");
            for(unsigned d=0;d<3;++d)if(!std::isfinite(area[d])||!std::isfinite(moment[d]))
                throw Refusal("original-face-nonfinite-local");
            if(Foam::mag(area-referenceVectorArea)>1e-10*referenceArea||std::abs(absArea-referenceArea)>1e-10*referenceArea
              ||Foam::mag(moment)>1e-10*referenceArea*scale)throw Refusal("original-face-official-moment");
        }
        std::set<Label> finalCells=affected;for(Label c:cellIds)finalCells.insert(c);
        closed(staged,finalCells,tick,scanTick);const auto after=geometry(staged,finalCells,tick,&geometryReference,scanTick);
        for(Label c:affected)if(c!=seed)
        {
            const auto& a=before.at(c);const auto& b=after.at(c);double scale=std::cbrt(a.volume);
            if(std::abs(a.volume-b.volume)>1e-10*a.volume||Foam::mag(b.volume*(b.centre-a.centre))>1e-10*a.volume*scale)throw Refusal("neighbour-official-moment");
        }
        const auto& origin=before.at(seed);double volume=0;Foam::vector moment=Foam::vector::zero;
        for(std::size_t i=0;i<cellIds.size();++i)
        {
            const auto& g=after.at(cellIds[i]);volume+=g.volume;moment+=g.volume*(g.centre-origin.centre);
            const auto& p=*parts[i];const double scale=std::cbrt(p.volume);
            Foam::point pc(p.volumeCentroid[0],p.volumeCentroid[1],p.volumeCentroid[2]);
            if(std::abs(g.volume-p.volume)>1e-10*p.volume||Foam::mag(g.volume*(g.centre-pc))>1e-10*p.volume*scale)throw Refusal("part-official-moment");
        }
        if(std::abs(volume-origin.volume)>1e-10*origin.volume
          ||Foam::mag(moment)>1e-10*origin.volume*std::cbrt(origin.volume))throw Refusal("parent-official-moment");
        for(Label p:seedPoints){tick();if(!completePointStar(p))throw Refusal("point-star-changed");}
        if(multipleSolid)
        {if(classify(output.corePoint))throw Refusal("fluid-kernel-membership-changed");}
        else if(!classify(output.solidKernel))throw Refusal("solid-kernel-membership-changed");
        // These coordinates belong to this exact installed partition. Later
        // approximate welding may not displace them, including reused corners.
        if(exactPointPins)
        {
            stagedPins.resize(staged.points.size(),0);
            for(auto n:used){tick();stagedPins.at(std::size_t(nodeIds[n]))=1;}
            for(std::size_t p=m.points.size();p<staged.points.size();++p)
            {tick();stagedPins[p]=1;}
        }
        if(exactConstruction)
        {
            for(const auto& item:exact)
            {
                tick();const Label id=item.first;
                if(id<0||std::size_t(id)>=staged.points.size())throw Refusal("install-construction-id");
                D::OriginalExactPoint value;value.exactPoint=item.second;
                value.storedPoint={{staged.points[id][0],staged.points[id][1],staged.points[id][2]}};
                value.surface=authority.window.surface;value.shell=authority.window.shell;
                value.geometryEpoch=authority.window.geometryEpoch;
                value.membershipEpoch=authority.window.membershipEpoch;
                if(!sameStored(X::rounded(value.exactPoint),value.storedPoint))
                    throw Refusal("install-construction-rounding");
                const auto found=stagedConstruction.find(id);
                if(found!=stagedConstruction.end()&&!sameConstruction(found->second,value))
                    throw Refusal("install-construction-redefinition");
                if(found==stagedConstruction.end())stagedConstruction.emplace(id,std::move(value));
            }
        }
        if(staged.preparedBirth.extractionEpoch>0)
        {
            // Metadata-only proof: all physical/identity/full-star/closed/V-M/
            // material checks above remain mandatory. Never flatten to all seed roots.
            std::size_t birthSteps=0;
            const std::size_t birthLimit=host?hostScanBudget(inputSizedHostLimits(staged)):2000000;
            auto birthTick=[&]()
            {
                if(++birthSteps>birthLimit||!authority.available||!authority.available()
                  ||!dcCartSignedCrossing::supportedFloatingEnvironment())
                    throw Refusal("install-birth-clock-fp-or-cap");
            };
            const char* birthRefusal=nullptr;
            const char* birthProfileMode=std::getenv("DCCART_PROFILE");
            const bool birthProfile=birthProfileMode&&std::strcmp(birthProfileMode,"1")==0;
            const auto birthStart=birthProfile?std::chrono::steady_clock::now():std::chrono::steady_clock::time_point{};
            const bool birthSealed=dcCartPreparedFacetBirth::seal(staged,m,seed,original,replacements,
                originalFaceAllowed,birthTick,&birthRefusal);
            if(birthProfile)std::fprintf(stderr,
                "Prepared typed-birth seal seed %d faces %zu records %zu newPoints %zu ticks %zu seconds %.9f sealed %d reason %s\n",
                seed,staged.faces.size(),staged.preparedBirth.records.size(),staged.points.size()-m.points.size(),birthSteps,
                std::chrono::duration<double>(std::chrono::steady_clock::now()-birthStart).count(),int(birthSealed),
                birthRefusal?birthRefusal:"none");
            if(!birthSealed)throw Refusal(birthRefusal?birthRefusal:"install-birth-unproven");
        }
        for(std::size_t part=fluidCount;part<count;++part)stagedSolid.insert(cellIds[part]);lease();
        // All potentially throwing checks and allocations precede publication.
        static_assert(std::is_nothrow_move_assignable<Mesh>::value,"native mesh publication must not throw");
        Mesh& publication=destination?*destination:m;
        publication=std::move(staged);side.swap(stagedSide);knownSolid.swap(stagedSolid);
        if(knownSolidOriginalFaces)knownSolidOriginalFaces->swap(stagedSolidOriginal);
        if(exactPointPins)exactPointPins->swap(stagedPins);
        if(exactConstruction)exactConstruction->swap(stagedConstruction);
        if(originalReceipts)originalReceipts->swap(stagedOriginalReceipts);
        return true;
    }
    catch(const std::exception& e){if(refusal){try{*refusal=e.what();}catch(...){}}return false;}
    catch(...){if(refusal){try{*refusal="install-unknown-refusal";}catch(...){}}return false;}
}
}
#endif
