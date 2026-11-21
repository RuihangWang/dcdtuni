#ifndef dcCartPostCarveFluidOwnershipPreview_H
#define dcCartPostCarveFluidOwnershipPreview_H
#include "dcCartWeightUnionSourceContract.H"
#include "dcCartFluidOriginSnapshotCore.H"
#include <functional>
#include <memory>
namespace Foam { namespace dcCartPostCarveFluidOwnershipPreview {
using Snapshot=dcCartFluidOriginSnapshotCore::Snapshot;
using Identity=dcCartFluidOriginSnapshotCore::Identity;
inline bool available(const std::function<scalar()>& remaining)
{
    if(!remaining)return false;
    const scalar seconds=remaining();
    return std::isfinite(seconds) && seconds>0;
}
inline std::shared_ptr<const Snapshot> capture
(
    const dcCartCutCells::cutMesh& m,const dictionary& meshDict,
    const wordList& patchNames,const wordList& patchTypes,const wordList& faceZoneNames,
    label myRank,bool completedNormalFluidOnlyPath,
    const std::function<scalar()>& remaining
)
{
    // This boolean is set only by successful APP cutCarving + completed normal
    // post-carve path, not an environment 'permission' or cellOrigAlive flag.
    auto s=std::make_shared<Snapshot>();
    const auto material=dcCartWeightUnionSourceContract::readLocal(meshDict);
    const auto necessary=dcCartPointRepairSourceContract::readLocal(meshDict);
    if (!completedNormalFluidOnlyPath || !necessary.valid || !necessary.eligible
     || !material.valid || !material.eligible || !available(remaining)) return s;
    if(patchNames.size()!=patchTypes.size())return s;
    std::set<std::string> names;
    forAll(patchNames,pi) {
        const std::string name=patchNames[pi],type=patchTypes[pi];
        if(name.empty() || !names.insert(name).second
         || !(type=="wall" || type=="patch" || type=="processor"
           || type=="cyclic" || type=="processorCyclic" || type=="cyclicAMI"))return s;
    }
    s->completedFluidOnlyPath=true;s->rawSha256=material.rawSha256;
    s->rolesDigest=material.rolesDigest;s->materialKey=material.materialKey;
    s->materialDigest=material.materialDigest;s->producerDigest=material.producerDigest;
    s->boundaryMembershipDigest=material.boundaryMembershipDigest;
    const label nc=m.nCells,nf=m.faces.size(),np=m.points.size();
    if(nc<0 || m.cellAlive.size()!=std::size_t(nc) || m.cellRank.size()!=std::size_t(nc)
     || m.cellLevel.size()!=std::size_t(nc) || m.cellLeaf.size()!=std::size_t(nc)
     || m.cellPiece.size()!=std::size_t(nc) || m.owner.size()!=std::size_t(nf)
     || m.neighbour.size()!=std::size_t(nf) || m.patch.size()!=std::size_t(nf)
     || m.zone.size()!=std::size_t(nf) || m.faceAlive.size()!=std::size_t(nf)
     || patchNames.size()!=patchTypes.size())return s;
    // Shared coordinates/rings are copied once, not once per parent. This
    // declared payload cap excludes allocator headers; no wall-time promise.
    std::size_t estimate=np*sizeof(dcCartFluidOriginSnapshotCore::Coord)
        +nf*sizeof(dcCartFluidOriginSnapshotCore::Facet)
        +nc*(sizeof(dcCartFluidOriginSnapshotCore::Parent)+1);
    for(label f=0;f<nf;++f) {
        if((f&1023)==0 && !available(remaining))return s;
        if(m.faceAlive[f]) {
            if(m.faces[f].size()>512)return s;
            estimate+=(m.faces[f].size()+4)*sizeof(int);
        }
        if(estimate>std::size_t(256)*1024*1024)return s;
    }
    s->points.resize(np);s->faces.resize(nf);s->parents.resize(nc);s->valid.assign(nc,0);
    for(label p=0;p<np;++p) {
        if((p&1023)==0 && !available(remaining))return std::make_shared<Snapshot>();
        const point& x=m.points[p];
        s->points[p]={{x.x(),x.y(),x.z()}};
        if(!std::isfinite(x.x()) || !std::isfinite(x.y()) || !std::isfinite(x.z()))
            return std::make_shared<Snapshot>();
    }
    for(label c=0;c<nc;++c) {
        if((c&1023)==0 && !available(remaining))return std::make_shared<Snapshot>();
        s->parents[c].identity=Identity(myRank,c,m.cellLevel[c],m.cellLeaf[c],m.cellPiece[c]);
        s->valid[c]=m.cellAlive[c] && m.cellRank[c]==myRank && m.cellLevel[c]>=0;
    }
    for(label f=0;f<nf;++f) {
        if((f&1023)==0 && !available(remaining))return std::make_shared<Snapshot>();
        if(!m.faceAlive[f])continue;
        const label own=m.owner[f],nei=m.neighbour[f],patch=m.patch[f],zone=m.zone[f];
        if(own<0 || own>=nc || !m.cellAlive[own] || nei>=nc || nei<-1
         || (nei>=0 && (nei==own || !m.cellAlive[nei])) || patch<-1 || zone<-1
         || (nei>=0 && patch!=-1) || (nei<0 && (patch<0 || patch>=patchNames.size()))
         || (zone>=0 && zone>=label(faceZoneNames.size())))return std::make_shared<Snapshot>();
        auto& face=s->faces[f];face.handle=f;face.owner=own;face.neighbour=nei;
        face.patch=patch;face.zone=zone;
        face.ring.assign(m.faces[f].begin(),m.faces[f].end());
        for(label id:m.faces[f])if(id<0 || id>=np)return std::make_shared<Snapshot>();
        s->parents[own].facets.push_back(f);
        if(nei>=0)s->parents[nei].facets.push_back(f);
    }
    // Full oriented shape is checked lazily only for bounded W candidate
    // origins, using this immutable pre-cone copy; no whole-parent quality scan.
    if(!available(remaining))return std::make_shared<Snapshot>();
    s->complete=true;
    return s;
}
inline dcCartCutCells::weightUnionOrigin origin
(
    const std::shared_ptr<const Snapshot>& s,label parent
)
{
    dcCartCutCells::weightUnionOrigin out;
    if(!s || !s->complete || parent<0 || parent>=label(s->parents.size())
     || !s->valid[parent] || !dcCartFluidOriginSnapshotCore::validShape(*s,s->parents[parent]))return out;
    const auto& record=s->parents[parent];
    out.rank=std::get<0>(record.identity);out.nativeParent=parent;
    out.level=std::get<2>(record.identity);out.leaf=std::get<3>(record.identity);
    out.piece=std::get<4>(record.identity);
    out.region=std::string(polyMesh::defaultRegion); // IO label only, not authority
    for(int f:record.facets) {
        const auto& face=s->faces[f];
        out.sourceFacets.push_back({{face.handle,face.patch,face.zone}});
    }
    return out;
}
inline dcCartWeightUnionSourceContract::NormalOriginVerifier verifier
(
    const std::shared_ptr<const Snapshot>& s
)
{
    return [s](const std::vector<dcCartCutCells::weightUnionOrigin>& origins,
               const dcCartWeightUnionSourceContract::GlobalMaterialContract& material)
    {
        if(!s || origins.size()!=2 || origins[0].nativeParent==origins[1].nativeParent)return false;
        for(const auto& o:origins) {
            if(o.region!=std::string(polyMesh::defaultRegion))return false;
            // Both original identities/complete cut facet roles and full
            // immutable oriented shape must match the current run's proof.
            Identity id(o.rank,o.nativeParent,o.level,o.leaf,o.piece);
            std::vector<std::array<int,3>> facets;
            for(const auto& f:o.sourceFacets)facets.push_back({{f[0],f[1],f[2]}});
            if(!dcCartFluidOriginSnapshotCore::verify(*s,id,facets,
                material.rawSha256,material.rolesDigest,material.materialKey,
                material.materialDigest,material.producerDigest))return false;
        }
        return true;
    };
}
} }
#endif
