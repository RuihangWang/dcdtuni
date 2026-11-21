#ifndef dcCartCompleteCavityAuthority_H
#define dcCartCompleteCavityAuthority_H
#include "dcCartPostCarveFluidOwnershipPreview.H"
#include "dcCartCompleteCavityOriginCore.H"
namespace Foam { namespace dcCartCompleteCavityAuthority
{
inline bool available(const std::function<scalar()>& remaining)
{
    if (!remaining) return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::authority,dcCartLegacyJournalObservation::Source::authority,9);
    const scalar seconds=remaining();
    return !dcCartLegacyJournalObservation::observe(!std::isfinite(seconds),dcCartLegacyJournalObservation::Reason::authority,dcCartLegacyJournalObservation::Source::authority,11)
        && !dcCartLegacyJournalObservation::observe(!(seconds>0),dcCartLegacyJournalObservation::Reason::authority,dcCartLegacyJournalObservation::Source::authority,11);
}
inline dcCartCutCells::completeCavityAuthority read
(
    const dictionary& meshDict,
    const std::shared_ptr<const dcCartPostCarveFluidOwnershipPreview::Snapshot>& snapshot,
    const dcCartCutCells::weightUnionAuthority& agreedPairAuthority,
    const std::function<scalar()>& actualRemainingSeconds
)
{
    dcCartCutCells::completeCavityAuthority out;
    const auto material=dcCartWeightUnionSourceContract::readLocal(meshDict);
    const auto necessary=dcCartPointRepairSourceContract::readLocal(meshDict);
    // Reuse the existing APP's all-rank dictionary agreement, not a second
    // collective or a global-bool permission. Complete normal shape/facet
    // binding and every fresh digest are independently required below.
    if (!agreedPairAuthority.globallyNoMaterialNoBulk
     || !agreedPairAuthority.homogeneousOrigins
     || !agreedPairAuthority.normalOriginFromSnapshot
     || !necessary.valid || !necessary.eligible || !material.valid || !material.eligible
     || !snapshot || !snapshot->complete || !snapshot->completedFluidOnlyPath
     || !available(actualRemainingSeconds)
     || agreedPairAuthority.rawSha256!=material.rawSha256
     || agreedPairAuthority.rolesDigest!=material.rolesDigest
     || agreedPairAuthority.homogeneousFluidKey!=material.materialKey) return out;
    const auto& absence=meshDict.subDict("dcdtuniPointRepairSourceRoleContract");
    const ITstream* raw=dcCartPointRepairSourceContract::primitive(absence,"rawSha256");
    const ITstream* roles=dcCartPointRepairSourceContract::primitive(absence,"rolesDigest");
    if (!raw || !roles || std::string((*raw)[0].stringToken())!=material.rawSha256
     || std::string((*roles)[0].stringToken())!=material.rolesDigest) return out;
    const dcCartCompleteCavityOriginCore::Fingerprint fresh
    {
        material.rawSha256,material.rolesDigest,material.materialKey,
        material.materialDigest,material.producerDigest,material.boundaryMembershipDigest
    };
    if (!dcCartCompleteCavityOriginCore::validFingerprint(fresh)
     || snapshot->rawSha256!=fresh.raw || snapshot->rolesDigest!=fresh.roles
     || snapshot->materialKey!=fresh.materialKey || snapshot->materialDigest!=fresh.material
     || snapshot->producerDigest!=fresh.producer
     || snapshot->boundaryMembershipDigest!=fresh.boundaryMembership) return out;
    const label actualRank=Pstream::myProcNo();
    out.sourceFingerprint=fresh;
    out.remainingSeconds=actualRemainingSeconds;
    out.recordedParent=[snapshot,actualRank](label parent)
    {
        if (parent<0 || parent>=label(snapshot->parents.size())
         || parent>=label(snapshot->valid.size()) || !snapshot->valid[parent]) return false;
        const auto& id=snapshot->parents[parent].identity;
        return std::get<0>(id)==actualRank && std::get<1>(id)==parent && std::get<2>(id)>=0;
    };
    out.normalOriginFromSnapshot=[snapshot](label parent)
        { return dcCartPostCarveFluidOwnershipPreview::origin(snapshot,parent); };
    out.completeOrigins=[snapshot,fresh,actualRank,actualRemainingSeconds]
        (const std::vector<dcCartCutCells::weightUnionOrigin>& origins)
    {
        if (origins.empty() || origins.size()>dcCartCompleteCavityOriginCore::maxOrigins) return false;
        std::vector<dcCartCompleteCavityOriginCore::Origin> records;
        records.reserve(origins.size());
        for (const auto& origin:origins)
        {
            if (origin.region!=std::string(polyMesh::defaultRegion)) return false;
            dcCartCompleteCavityOriginCore::Origin record;
            record.identity=dcCartFluidOriginSnapshotCore::Identity
                (origin.rank,origin.nativeParent,origin.level,origin.leaf,origin.piece);
            if (origin.sourceFacets.size()>512) return false;
            for (const auto& face:origin.sourceFacets)
                record.facets.push_back({{face[0],face[1],face[2]}});
            records.push_back(std::move(record));
        }
        return dcCartCompleteCavityOriginCore::verifyMany
            (*snapshot,records,fresh,actualRank,[actualRemainingSeconds]()
                { return available(actualRemainingSeconds); });
    };
    return out;
}
// A future topology consumer must still prove the complete current cavity,
// exact shell/coverage and all installed guards. This function grants only
// current live-cell -> immutable complete-origin permission, never geometry.
inline bool collectCompleteOrigins
(
    const dcCartCutCells::cutMesh& mesh,
    const dcCartCutCells::completeCavityAuthority& authority,
    const std::vector<label>& cells,
    std::vector<dcCartCutCells::weightUnionOrigin>& origins
)
{
    origins.clear();
    // Reject oversized/empty caller input before any clock/mesh/shape scan.
    if (cells.empty() || cells.size()>dcCartCompleteCavityOriginCore::maxCavityCells) return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::capacity,dcCartLegacyJournalObservation::Source::authority,99);
    if (dcCartLegacyJournalObservation::observe(!authority.ready(),dcCartLegacyJournalObservation::Reason::authority,dcCartLegacyJournalObservation::Source::authority,100) || !available(authority.remainingSeconds)
     || mesh.nCells<0 || mesh.cellAlive.size()!=std::size_t(mesh.nCells)
     || mesh.completeCavityLineage.size()!=std::size_t(mesh.nCells)) return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::shape,dcCartLegacyJournalObservation::Source::authority,102);
    for (label cell:cells)
        if (cell<0 || cell>=mesh.nCells || !mesh.cellAlive[cell]) return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::shape,dcCartLegacyJournalObservation::Source::authority,104);
    std::vector<label> parents;
    if (!mesh.completeCavityLineage.originsForCells(cells,parents)) return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::origin,dcCartLegacyJournalObservation::Source::authority,106);
    for (label parent:parents)
    {
        if (!available(authority.remainingSeconds) || !authority.recordedParent(parent))
        { origins.clear();return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::origin,dcCartLegacyJournalObservation::Source::authority,110); }
        origins.push_back(authority.normalOriginFromSnapshot(parent));
    }
    if (!authority.completeOrigins(origins)) { origins.clear();return dcCartLegacyJournalObservation::reject(dcCartLegacyJournalObservation::Reason::origin,dcCartLegacyJournalObservation::Source::authority,113); }
    return true;
}
} }
#endif
