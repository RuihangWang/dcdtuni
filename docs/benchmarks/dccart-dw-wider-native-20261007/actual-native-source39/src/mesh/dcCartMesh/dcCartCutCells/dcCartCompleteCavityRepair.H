#ifndef dcCartCompleteCavityRepair_H
#define dcCartCompleteCavityRepair_H
#include "dcCartCompleteCavityTwoLobe.H"
namespace Foam { namespace dcCartCompleteCavityRepair
{
namespace O=dcCartCompleteCavityLocalOracle;
namespace P=dcCartCompleteCavityPreparedTopology;
namespace T=dcCartCompleteCavityTwoLobe;
struct Result
{
    label current=0,attempted=0,generated=0,prepared=0,committed=0;
    std::array<label,12> refused{{0}};
    std::size_t preparedBytes=0,planeBases=0;
    scalar seconds=0;
};
// Every trial is local. Source/geometry/metadata/alias/Lineage/journal staging
// completes BEFORE a single noexcept publication; no toPolyMesh/trial MPI.
inline Result apply(dcCartCutCells::cutMesh& live,
                    const dcCartCompleteCavityLocalOracle::Catalogue& catalogue,
                    const dcCartCutCells::completeCavityAuthority& authority,
                    label unjournalledLegacy,
                    const std::vector<std::vector<label>>& currentCellFaces)
{
    const auto publicationBudget=P::PublicationBudget::capture(authority.remainingSeconds);
    clockTime stage;Result result;
    auto awake=[&]()
    {
        try{return publicationBudget.available()&&stage.elapsedTime()<4 && O::available(authority);}
        catch(...){return false;}
    };
    for(const auto& view:catalogue.views)
    {
        if(!awake()||result.attempted>=4||result.committed)break;
        if(!O::current(view,live,authority,unjournalledLegacy,currentCellFaces))
        {++result.refused[unsigned(P::Refusal::stale)];continue;}
        ++result.current;++result.attempted;
        P::Recipe recipe;T::Discovery discovery;
        if(!T::generate(view,live,awake,recipe,discovery))
        {++result.refused[unsigned(discovery.timeout?P::Refusal::clock:P::Refusal::shape)];continue;}
        ++result.generated;result.planeBases+=discovery.planeBases;
        P::Prepared candidate;
        if(!P::prepare(live,view,authority,unjournalledLegacy,currentCellFaces,
                       std::move(recipe),publicationBudget,awake,candidate))
        {++result.refused[unsigned(candidate.refusal())];continue;}
        ++result.prepared;result.preparedBytes=candidate.preparedBytes();
        if(!P::commit(live,authority,awake,candidate))
        {++result.refused[unsigned(P::Refusal::stale)];continue;}
        ++result.committed;
    }
    result.seconds=stage.elapsedTime();return result;
}
// Read-only REAL installed-writer admission, prefix and post-point-repair.
// Numeric lowering and final mappings remain normal writer-owned. No guessed
// compact indices, artificial boundary permission, TRACE or remote quality.
inline label verify(const dcCartCutCells::cutMesh& live,const polyMesh& actual,
                    const O::Bindings& bindings,
                    const dcCartCutCells::writerCompleteCavityState& state,
                    P::Certificate& report)
{
    report=P::Certificate();
    const auto& view=state.nextView;const auto& authority=state.authority;
    auto awake=[&](){try{return O::available(authority);}catch(...){return false;}};
    try
    {
        if(!view.ready()||!awake()||!state.certificate.installedArithmetic
         ||!state.certificate.exactMoments||!state.certificate.disjointCoverage
         ||live.nCells!=view.nativeCells||live.points.size()!=std::size_t(view.nativePoints)
         ||bindings.cell.size()!=std::size_t(live.nCells)||bindings.face.size()!=live.faces.size()
         ||bindings.point.size()!=live.points.size()||bindings.alias.size()!=live.points.size()
         ||live.faceAlive.size()!=live.faces.size())return 1;
        const auto& fresh=authority.sourceFingerprint;const auto& old=state.oldView.source;
        if(fresh.raw!=old.raw||fresh.roles!=old.roles||fresh.materialKey!=old.materialKey
         ||fresh.material!=old.material||fresh.producer!=old.producer
         ||fresh.boundaryMembership!=old.boundaryMembership)return 1;
        std::vector<O::Origin> origins;
        if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(live,authority,view.core,origins)
         ||origins.size()!=state.oldView.origins.size())return 1;
        for(std::size_t i=0;i<origins.size();++i)
        {
            const auto& a=origins[i];const auto& b=state.oldView.origins[i];
            if(a.nativeParent!=b.nativeParent||a.rank!=b.rank||a.level!=b.level||a.leaf!=b.leaf
             ||a.piece!=b.piece||a.region!=b.region||a.sourceFacets!=b.sourceFacets)return 1;
        }
        std::map<label,label> inverseCell,inverseFace;
        O::View installed=view;
        for(auto& c:installed.cells)
        {
            if(!awake()||c.native<0||c.native>=live.nCells||!live.cellAlive[c.native]
             ||live.cellRank[c.native]!=view.actualRank||bindings.cell[c.native]<0
             ||bindings.cell[c.native]>=actual.nCells()
             ||!inverseCell.emplace(bindings.cell[c.native],c.native).second
             ||dcCartWeightUnionPreview::zonedCell(actual,bindings.cell[c.native]))return 1;
            c.ordinal=bindings.cell[c.native];c.faces.clear();
        }
        for(auto& f:installed.faces)
        {
            if(!awake()||f.native<0||f.native>=label(live.faces.size())||!live.faceAlive[f.native]
             ||bindings.face[f.native]<0||bindings.face[f.native]>=actual.nFaces()
             ||!inverseFace.emplace(bindings.face[f.native],f.native).second
             ||live.patch[f.native]!=f.patch||live.zone[f.native]!=f.zone
             ||O::protectedRole(f)||dcCartWeightUnionPreview::zonedFace(actual,bindings.face[f.native]))return 1;
            // Normal lowering mutates raw face aliases and compact endpoints.
            // Source f.owner/f.rawOwner remain native identities for authority.
            if(f.rawOwner<0||f.rawOwner>=label(bindings.cell.size())
             ||f.rawNeighbour<-1||f.rawNeighbour>=label(bindings.cell.size()))return 1;
            label expectedOwner=bindings.cell[f.rawOwner];
            label expectedNeighbour=f.rawNeighbour>=0?bindings.cell[f.rawNeighbour]:-1;
            std::vector<label> expectedRing;expectedRing.reserve(f.rawRing.size());
            for(label p:f.rawRing)
            {
                if(p<0||p>=label(bindings.alias.size()))return 1;
                const label rep=bindings.alias[p];
                if(rep<0||rep>=label(bindings.alias.size())||bindings.alias[rep]!=rep)return 1;
                expectedRing.push_back(rep);
            }
            if(expectedOwner<0)
            {
                if(expectedNeighbour<0)return 1;
                std::reverse(expectedRing.begin(),expectedRing.end());
                expectedOwner=expectedNeighbour;expectedNeighbour=-1;
            }
            if(expectedNeighbour>=0&&expectedOwner>expectedNeighbour)
            {std::reverse(expectedRing.begin(),expectedRing.end());std::swap(expectedOwner,expectedNeighbour);}
            if(live.owner[f.native]!=expectedOwner||live.neighbour[f.native]!=expectedNeighbour
             ||live.faces[f.native]!=expectedRing||f.ring!=expectedRing)return 1;
            const label fi=bindings.face[f.native];
            if(f.internal!=(fi<actual.nInternalFaces())||dcCartWeightUnionPreview::coupled(actual,fi))return 1;
            if(f.zone>=0&&!O::immutablePhysicalRole(live,authority,f))return 1;
            f.ordinal=fi;
            if(f.owner<0||f.owner>=label(bindings.cell.size())
             ||actual.faceOwner()[fi]!=bindings.cell[f.owner]
             ||(f.internal&&(f.neighbour<0||f.neighbour>=label(bindings.cell.size())
                ||actual.faceNeighbour()[fi]!=bindings.cell[f.neighbour])))return 1;
            f.ownerOrdinal=actual.faceOwner()[fi];f.neighbourOrdinal=f.internal?actual.faceNeighbour()[fi]:-1;
            if(!f.internal)
            {
                const label patch=actual.boundaryMesh().patchID(fi);
                if(patch<0||dcCartWeightUnionPreview::patchRole(actual.boundaryMesh()[patch])!=f.patchRole)return 1;
            }
            const auto& ring=actual.faces()[fi];
            if(ring.size()!=label(f.ring.size()))return 1;
            forAll(ring,k)
            {
                const label rep=f.ring[k];const auto* a=P::alias(view,rep);
                if(!a||rep<0||rep>=label(bindings.alias.size())||bindings.alias[rep]!=rep
                 ||bindings.point[rep]!=ring[k]||!O::bits(actual.points()[ring[k]],a->world))return 1;
            }
        }
        // Alias equivalence/world representatives include unused former APs:
        // they need not have a final compact point, but cannot gain/lose members.
        std::map<label,std::size_t> classes;
        for(std::size_t i=0;i<view.aliases.size();++i)classes.emplace(view.aliases[i].representative,i);
        std::vector<std::vector<label>> finalMembers(view.aliases.size());
        for(std::size_t p=0;p<bindings.alias.size();++p)
        {
            if((p&255)==0&&!awake())return 1;
            const auto found=classes.find(bindings.alias[p]);
            if(found!=classes.end())finalMembers[found->second].push_back(p);
        }
        for(std::size_t i=0;i<view.aliases.size();++i)
        {
            const auto& a=view.aliases[i];
            if(!awake()||a.members.size()!=a.rawWorld.size()||a.members.empty()
             ||a.members!=finalMembers[i])return 1;
            for(std::size_t k=0;k<a.members.size();++k)
            {
                const label p=a.members[k];
                if(p<0||p>=label(bindings.alias.size())||bindings.alias[p]!=a.representative
                 ||!O::bits(live.points[p],a.rawWorld[k]))return 1;
            }
        }
        for(auto& c:installed.cells)
        {
            for(label fi:actual.cells()[c.ordinal])
            {
                const auto found=inverseFace.find(fi);if(found==inverseFace.end())return 1;
                c.faces.push_back(found->second);
            }
            auto expected=c.rawFaces,actualFaces=c.faces;
            std::sort(expected.begin(),expected.end());std::sort(actualFaces.begin(),actualFaces.end());
            if(expected!=actualFaces)return 1;
            c.centre=actual.cellCentres()[c.ordinal];c.volume=actual.cellVolumes()[c.ordinal];
            c.determinant=dcCartWeightUnionPreview::determinant(actual,c.ordinal);
            if(!O::finite(c.centre)||!O::finite(c.volume)||!O::finite(c.determinant)||c.volume<=VSMALL)return 1;
        }
        for(auto& f:installed.faces)
        {
            f.centre=actual.faceCentres()[f.ordinal];f.area=actual.faceAreas()[f.ordinal];
            if(f.metric)f.quality=dcCartWeightUnionPreview::metrics(actual,f.ordinal);
        }
        O::Values arithmetic;
        if(!O::evaluate(installed,{},awake,arithmetic,
          [&](const O::GeometryMesh& g,const std::map<label,label>& cells,const std::map<label,label>& faces)
          {return P::installedGeometry(g,cells,faces,installed,awake);})
         ||!O::installedBinding(installed,arithmetic))return 1;
        report.before=state.certificate.before;report.after=std::move(arithmetic);
        if(!P::hardLineage(state.oldView,installed,report))return 1;
        report.installedArithmetic=true;report.exactMoments=state.certificate.exactMoments;
        report.disjointCoverage=state.certificate.disjointCoverage;return 0;
    }
    catch(...){return 1;}
}
} }
#endif
