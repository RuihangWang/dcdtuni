// Metadata-only original STL region -> final native boundary range binding.
// No sourceFaceUid is generated here. Each row names an entire original region
// facet inventory, not the ancestor triangle of every final cut face.
#ifndef dcCartSourceRegions_H
#define dcCartSourceRegions_H
#include "OFstream.H"
#include "clockTime.H"
#include <set>
#include <vector>
#include <algorithm>
namespace Foam
{
inline void writeDcCartSourceRegions
(
    const Time& runTime, const polyMesh& mesh, const dcCartSurfaces& surfaces,
    const dictionary& catalogue, const wordList& authoredPatchNames,
    const labelList& wallMasters,
    const labelList& wallSlaves, const labelList& cycMasters,
    const labelList& cycSlaves, const labelList& writtenPatchAddressMap
)
{
    clockTime provenanceTimer;
    struct Row
    {
        string entity, region, sha;
        word file, solid, operation;
        label surfaceI, localRegionI, facets;
        std::vector<label> patches;
        word unresolved;
    };
    std::vector<Row> rows;
    std::set<std::string> regionUids;
    for (const entry& fileEntry : catalogue)
    {
        if (!fileEntry.isDict() || fileEntry.keyword().isPattern())
        {
            FatalErrorInFunction << "dcdtuniSourceRegions requires literal file dictionaries"
                << exit(FatalError);
        }
        label surfaceI = -1;
        forAll(surfaces.info(), si)
        {
            if (surfaces.info()[si].stlFile != fileEntry.keyword()) { continue; }
            if (surfaceI >= 0)
            {
                FatalErrorInFunction << "ambiguous native source STL file "
                    << fileEntry.keyword() << exit(FatalError);
            }
            surfaceI = si;
        }
        if (surfaceI < 0)
        {
            FatalErrorInFunction << "unknown native source STL file "
                << fileEntry.keyword() << exit(FatalError);
        }
        const auto& surface = surfaces.info()[surfaceI];
        const wordList regions(surfaces.sourceRegionNames(surfaceI));
        for (const entry& regionEntry : fileEntry.dict())
        {
            if (!regionEntry.isDict() || regionEntry.keyword().isPattern())
            {
                FatalErrorInFunction << "dcdtuniSourceRegions requires literal region dictionaries"
                    << exit(FatalError);
            }
            const dictionary& fields = regionEntry.dict();
            const std::set<std::string> keys = {"sourceEntityUid", "sourceRegionUid",
                "sourceRegionSha256", "sourceFacetCount", "operation"};
            for (const entry& field : fields)
            {
                if (field.isDict() || field.keyword().isPattern()
                    || !keys.count(std::string(field.keyword().c_str())))
                {
                    FatalErrorInFunction << "unknown source-region metadata field "
                        << field.keyword() << exit(FatalError);
                }
            }
            Row row;
            row.file = fileEntry.keyword(); row.solid = regionEntry.keyword();
            row.surfaceI = surfaceI; row.localRegionI = -1;
            forAll(regions, ri)
            {
                if (regions[ri] != row.solid) { continue; }
                if (row.localRegionI >= 0)
                {
                    FatalErrorInFunction << "ambiguous native LOCAL STL region "
                        << row.file << '/' << row.solid << exit(FatalError);
                }
                row.localRegionI = ri;
            }
            if (row.localRegionI < 0)
            {
                FatalErrorInFunction << "unknown native LOCAL STL region "
                    << row.file << '/' << row.solid << exit(FatalError);
            }
            row.entity = fields.get<string>("sourceEntityUid", keyType::LITERAL);
            row.region = fields.get<string>("sourceRegionUid", keyType::LITERAL);
            row.sha = fields.get<string>("sourceRegionSha256", keyType::LITERAL);
            row.facets = fields.get<label>("sourceFacetCount", keyType::LITERAL);
            row.operation = fields.get<word>("operation", keyType::LITERAL);
            const string uidChars("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./:-");
            if (row.entity.empty() || row.region.empty()
                || row.entity.find_first_not_of(uidChars) != string::npos
                || row.region.find_first_not_of(uidChars) != string::npos
                || row.sha.size() != 64 || row.sha.find_first_not_of("0123456789abcdef") != string::npos
                || row.facets <= 0 || row.facets != surfaces.sourceRegionFacetCount(surfaceI, row.localRegionI)
                || !regionUids.insert(std::string(row.region.c_str())).second
                || (row.operation != "modelBoundary" && row.operation != "cellZone"
                    && row.operation != "doorSheet" && row.operation != "liquidTransportUnionBoundary"))
            {
                FatalErrorInFunction << "invalid native source-region identity/facet inventory "
                    << row.file << '/' << row.solid << exit(FatalError);
            }
            if (surface.nRegions == 0)
            {
                row.unresolved = "refinement-volume-has-no-boundary-range";
            }
            else if (surface.internalBaffle)
            {
                // These addresses were constructed from this surface's actual
                // faceZone. A multi-region zone maps canonically to its FULL
                // region catalogue, never to a guessed nearest triangle.
                const label zone = surfaces.faceZoneNames().find(surface.faceZone);
                if (zone >= 0)
                {
                    if (wallMasters[zone] >= 0) { row.patches.push_back(wallMasters[zone]); }
                    if (wallSlaves[zone] >= 0) { row.patches.push_back(wallSlaves[zone]); }
                    if (cycMasters[zone] >= 0) { row.patches.push_back(cycMasters[zone]); }
                    if (cycSlaves[zone] >= 0) { row.patches.push_back(cycSlaves[zone]); }
                }
                if (row.patches.empty()) { row.unresolved = "baffle-pair-not-written-by-this-native-job"; }
            }
            else
            {
                // Actual native region construction address, before the writer's
                // empty-patch compaction and duplicate-name fold.
                const label patchi = surface.regionOffset + row.localRegionI;
                if (row.localRegionI >= surface.nRegions || patchi < 0 || patchi >= surfaces.nPatches())
                { row.unresolved = "native-region-construction-address-invalid"; }
                else { row.patches.push_back(patchi); }
            }
            std::vector<label> writtenPatches;
            for (const label constructedPatchi : row.patches)
            {
                if (constructedPatchi < 0 || constructedPatchi >= authoredPatchNames.size()
                    || constructedPatchi >= writtenPatchAddressMap.size())
                { row.unresolved = "native-region-construction-address-invalid"; break; }
                const label writtenPatchi = writtenPatchAddressMap[constructedPatchi];
                if (writtenPatchi < 0)
                { row.unresolved = "native-region-boundary-removed-by-writer"; break; }
                if (writtenPatchi >= mesh.boundaryMesh().size()
                    || mesh.boundaryMesh()[writtenPatchi].name() != authoredPatchNames[constructedPatchi])
                { row.unresolved = "native-region-written-address-mismatch"; break; }
                writtenPatches.push_back(writtenPatchi);
            }
            row.patches = std::move(writtenPatches);
            if (!row.unresolved.empty()) { row.patches.clear(); }
            std::sort(row.patches.begin(), row.patches.end());
            row.patches.erase(std::unique(row.patches.begin(), row.patches.end()), row.patches.end());
            rows.push_back(std::move(row));
        }
    }
    std::sort(rows.begin(), rows.end(), [](const Row& a, const Row& b)
        { return a.region < b.region; });
    OFstream out(runTime.path()/runTime.constant()/"dcCartSourceRegions.json");
    out << "{\n  \"format\": \"dcdtuni.native-source-regions/v1\",\n"
        << "  \"rank\": " << Pstream::myProcNo() << ",\n  \"rankCount\": " << Pstream::nProcs()
        << ",\n  \"bindingGranularity\": \"patch-region-facet-inventory\",\n  \"regions\": [\n";
    for (size_t i = 0; i < rows.size(); ++i)
    {
        const Row& row = rows[i];
        out << (i ? ",\n" : "") << "    {\"sourceEntityUid\": \"" << row.entity.c_str()
            << "\", \"sourceRegionUid\": \"" << row.region.c_str()
            << "\", \"sourceRegionSha256\": \"" << row.sha.c_str()
            << "\", \"sourceFacetCount\": " << row.facets
            << ", \"sourceFacetUidPrefix\": \"" << row.region.c_str() << "/facet/\""
            << ", \"stlFile\": \"" << row.file << "\", \"solidName\": \"" << row.solid
            << "\", \"operation\": \"" << row.operation
            << "\", \"nativeSurfaceIndex\": " << row.surfaceI
            << ", \"nativeLocalRegionIndex\": " << row.localRegionI
            << ", \"unresolvedReason\": " << (row.unresolved.empty() ? "null" : "\"")
            << row.unresolved.c_str()
            << (row.unresolved.empty() ? "" : "\"") << ", \"patchRanges\": [";
        for (size_t j = 0; j < row.patches.size(); ++j)
        {
            const label patchi = row.patches[j];
            const auto& patch = mesh.boundaryMesh()[patchi];
            out << (j ? ", " : "") << "{\"patchIndex\": " << patchi
                << ", \"patchName\": \"" << patch.name() << "\", \"startFace\": "
                << patch.start() << ", \"nFaces\": " << patch.size() << "}";
        }
        out << "]}";
    }
    out << "\n  ]\n}\n"; out.flush();
    if (!returnReduce(out.good(), andOp<bool>()))
    {
        FatalErrorInFunction << "failed to write native source-region catalogue on every rank"
            << exit(FatalError);
    }
    const scalar elapsed = returnReduce(scalar(provenanceTimer.elapsedTime()), maxOp<scalar>());
    Info<< "Source-region metadata: " << rows.size() << " original inventories per rank ("
        << elapsed << " s max rank)" << nl;
}
}
#endif
