#ifndef dcCartPhysicalPointFacetSource_H
#define dcCartPhysicalPointFacetSource_H
#include "dcCartCompleteCavityLocalOracle.H"
namespace Foam { namespace dcCartPhysicalPointFacetSource
{
namespace O=dcCartCompleteCavityLocalOracle;
struct Witness
{
    label nativeFace=-1,sourceFacet=-1,nativeOwner=-1,constructionPatch=-1,zone=-1;
    std::vector<dcCartCutCells::weightUnionOrigin> origins;
    std::vector<label> sourceRing;
    std::vector<point> sourceWorld;
};
// Source-role evidence only. It grants no motion, geometry, journal or budget exemption.
// raw is the owned pre-lowering native recipe, never the writer-mutated compact owner array.
inline bool bind
(
    const dcCartCutCells::cutMesh& raw,const polyMesh& actual,
    const dcCartCutCells::writerBindings& binding,const O::NormalIndex& index,
    const dcCartCutCells::completeCavityAuthority& authority,
    const O::Catalogue& catalogue,const labelList& constructionToPatch,
    label nativeFace,Witness& out
)
{
    out=Witness();
    try
    {
        const std::size_t nf=raw.faces.size(),np=raw.points.size();
        const auto& snapshot=authority.sourceSnapshot;
        const auto& stamp=authority.sourceFingerprint;
        if(!snapshot||!authority.sourceSnapshotIsRecorded||!authority.sourceSnapshotIsRecorded(snapshot.get())
         ||!snapshot->complete||!snapshot->completedFluidOnlyPath
         ||snapshot->rawSha256!=stamp.raw||snapshot->rolesDigest!=stamp.roles
         ||snapshot->materialKey!=stamp.materialKey||snapshot->materialDigest!=stamp.material
         ||snapshot->producerDigest!=stamp.producer||snapshot->boundaryMembershipDigest!=stamp.boundaryMembership)return false;
        if(!catalogue.normalFacetSourcesComplete||!catalogue.epoch
         ||!O::sameFingerprint(catalogue.normalFacetSource,authority.sourceFingerprint)
         ||!dcCartCompleteCavityOriginCore::validFingerprint(authority.sourceFingerprint)
         ||!O::available(authority)||!index.ready||index.actual!=&actual||index.bindings!=&binding
         ||catalogue.normalFacetSources.size()!=nf||raw.owner.size()!=nf||raw.neighbour.size()!=nf
         ||raw.patch.size()!=nf||raw.zone.size()!=nf||raw.faceAlive.size()!=nf
         ||binding.face.size()!=nf||binding.cell.size()!=std::size_t(raw.nCells)
         ||binding.alias.size()!=np||binding.point.size()!=np
         ||nativeFace<0||nativeFace>=label(nf)||!raw.faceAlive[nativeFace])return false;
        for(const auto& composite:raw.physicalCompositeCarriers)
            if(!composite||composite->record().carrier==nativeFace)return false;
        const label f=binding.face[nativeFace],base=catalogue.normalFacetSources[nativeFace];
        if(f<actual.nInternalFaces()||f>=actual.nFaces()||base<0
         ||f>=label(index.nativeFace.size())||index.nativeFace[f]!=nativeFace)return false;
        const label patch=raw.patch[nativeFace],zone=raw.zone[nativeFace];
        if(patch<0||patch>=constructionToPatch.size()||zone<-1)return false;
        const label writtenPatch=constructionToPatch[patch];
        if(writtenPatch<0||writtenPatch>=actual.boundaryMesh().size()
         ||actual.boundaryMesh().whichPatch(f)!=writtenPatch
         ||actual.boundaryMesh()[writtenPatch].coupled())return false;
        const label compactOwner=actual.faceOwner()[f];
        if(compactOwner<0||compactOwner>=label(index.nativeCell.size()))return false;
        const label owner=index.nativeCell[compactOwner];
        if(owner<0||owner>=raw.nCells||binding.cell[owner]!=compactOwner
         ||raw.owner[nativeFace]!=owner||raw.neighbour[nativeFace]>=0)return false;
        const auto& oldRing=raw.faces[nativeFace];const auto& writtenRing=actual.faces()[f];
        if(oldRing.size()<3||oldRing.size()>O::Caps::ring||oldRing.size()!=std::size_t(writtenRing.size()))return false;
        std::set<label> representatives;
        for(std::size_t k=0;k<oldRing.size();++k)
        {
            const label nativePoint=oldRing[k];
            if(nativePoint<0||nativePoint>=label(np))return false;
            const label rep=binding.alias[nativePoint];
            if(rep<0||rep>=label(np)||binding.alias[rep]!=rep||!representatives.insert(rep).second)return false;
            const label compactPoint=binding.point[rep];
            if(compactPoint<0||compactPoint>=actual.nPoints()||compactPoint!=writtenRing[k]
             ||compactPoint>=label(index.nativePoint.size())||index.nativePoint[compactPoint]!=rep
             ||!O::bits(raw.points[nativePoint],raw.points[rep])
             ||!O::bits(raw.points[rep],actual.points()[compactPoint]))return false;
        }
        std::vector<dcCartCutCells::weightUnionOrigin> origins;
        if(!dcCartCompleteCavityAuthority::collectCompleteOrigins(raw,authority,{owner},origins))return false;
        std::size_t matches=0;
        for(const auto& origin:origins)for(const auto& facet:origin.sourceFacets)
            if(facet[0]==base&&facet[1]==patch&&facet[2]==zone)++matches;
        if(matches!=1||base>=label(snapshot->faces.size())||!O::available(authority))return false;
        const auto& source=snapshot->faces[base];
        if(source.handle!=base||source.patch!=patch||source.zone!=zone||source.neighbour!=-1
         ||source.owner<0||source.ring.size()<3||source.ring.size()>512)return false;
        bool owned=false;
        for(const auto& origin:origins)if(origin.nativeParent==source.owner)owned=true;
        if(!owned)return false;
        std::vector<point> world;world.reserve(source.ring.size());
        for(std::size_t k=0;k<source.ring.size();++k)
        {
            if((k&63)==0&&!O::available(authority))return false;
            const label p=source.ring[k];if(p<0||p>=label(snapshot->points.size()))return false;
            const auto& q=snapshot->points[p];const point x(q[0],q[1],q[2]);
            if(!O::finite(x))return false;world.push_back(x);
        }
        if(!O::available(authority))return false;
        out.sourceRing.assign(source.ring.begin(),source.ring.end());out.sourceWorld=std::move(world);
        out.nativeFace=nativeFace;out.sourceFacet=base;out.nativeOwner=owner;
        out.constructionPatch=patch;out.zone=zone;out.origins=std::move(origins);return true;
    }
    catch(...){out=Witness();return false;}
}
} }
#endif
