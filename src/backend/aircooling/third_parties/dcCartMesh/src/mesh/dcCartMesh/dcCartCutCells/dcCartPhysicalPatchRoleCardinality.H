#ifndef dcCartPhysicalPatchRoleCardinality_H
#define dcCartPhysicalPatchRoleCardinality_H
#include "polyMesh.H"
#include "polyBoundaryMesh.H"
#include "polyPatch.H"
#include "dictionary.H"
#include "IStringStream.H"
#include "OStringStream.H"
#include "ITstream.H"
#include "token.H"
#include <map>
#include <vector>
#include <string>
#include <functional>
#include <limits>
namespace Foam { namespace dcCartPhysicalPatchRoleCardinality {
// Final-layout metadata only. This is not Source, peer, geometry or publication authority.
struct Layout {label sdkPatch=-1,count=-1;std::string name,type;};
struct Counts {
    bool complete=false;const void* nativeMesh=nullptr;const void* sdkMesh=nullptr;
    const void* faceBinding=nullptr;const void* sdkView=nullptr;
    std::map<label,Layout> raw;
    bool lookup(const void* view,label rawPatch,label installedPatch,
        const std::string& name,const std::string& type,label& count)const
    {
        if(!complete||view!=sdkView)return false;
        const auto found=raw.find(rawPatch);
        if(found==raw.end()||found->second.sdkPatch!=installedPatch
            ||found->second.name!=name||found->second.type!=type||found->second.count<0)return false;
        count=found->second.count;return true;
    }
};
// oldRole is immutable recorded SDK metadata. Rebuild its one dynamic quantity;
// all other serialized entries retain their original values and order.
inline bool rewrittenRole(const std::string& oldRole,label predicted,std::string& result) noexcept
{
    result.clear();
    try {
        if(predicted<0||oldRole.empty()||oldRole.size()>16384)return false;
        IStringStream input(oldRole);dictionary role(input);
        ITstream* size=role.findStream("nFaces",keyType::LITERAL);
        if(!size||size->size()!=1||!(*size)[0].isLabel()||(*size)[0].labelToken()<0)return false;
        if(!role.set("nFaces",predicted))return false;
        OStringStream output;output.precision(std::numeric_limits<scalar>::max_digits10);
        role.writeEntries(output);result=output.str();return true;
    }catch(...){result.clear();return false;}
}
inline bool matchesPhysicalRole(const std::string& oldRole,const std::string& actualRole,
    label independentlyPredictedCount) noexcept
{
    std::string expected;
    return rewrittenRole(oldRole,independentlyPredictedCount,expected)&&expected==actualRole;
}
// Reuse the genuine complete face inverse already constructed by the SDK
// capture. Native eligible faces are counted independently of SDK role strings;
// SDK boundary coverage is a separate reverse check. Raw same-name patches are
// folded only through the writer's actual patchOf map.
template<class Native,class Bindings,class PatchMap,class Names,class Types>
bool captureNativeBindings(const Native& native,const polyMesh& mesh,const Bindings& bindings,
    const std::vector<label>& inverse,const std::vector<label>& inverseCells,
    const void* sdkView,const PatchMap& patchOf,
    const Names& expectedNames,const Types& expectedTypes,
    const std::function<bool(label)>& coupledSDKPatch,const std::function<bool()>& work,
    Counts& out,const char** why=nullptr)
{
    out=Counts();
    const auto fail=[&](const char* reason){out=Counts();if(why)*why=reason;return false;};
    try {
        if(!work||!work()||!sdkView||!coupledSDKPatch
            ||bindings.face.size()!=native.faces.size()
            ||native.owner.size()!=native.faces.size()||native.neighbour.size()!=native.faces.size()
            ||native.patch.size()!=native.faces.size()||native.faceAlive.size()!=native.faces.size()
            ||inverse.size()!=std::size_t(mesh.nFaces())||inverseCells.size()!=std::size_t(mesh.nCells())
            ||expectedNames.size()!=mesh.boundaryMesh().size()
            ||expectedTypes.size()!=mesh.boundaryMesh().size())
            return fail("physical-patch-layout-domain");
        std::vector<label> count(mesh.boundaryMesh().size(),0),covered(count.size(),0);
        for(label nf=0;nf<label(native.faces.size());++nf)
        {
            if((nf&1023)==0&&!work())return fail("physical-patch-layout-clock");
            const label mapped=bindings.face[nf];
            if(mapped>=0&&(mapped>=mesh.nFaces()||inverse[mapped]!=nf))
                return fail("physical-patch-layout-complete-face-inverse");
            if(!native.faceAlive[nf]||native.neighbour[nf]>=0)continue;
            const label rp=native.patch[nf],o=native.owner[nf];
            if(rp<0||std::size_t(rp)>=std::size_t(patchOf.size()))
                return fail("physical-patch-layout-raw-patch");
            const label pi=patchOf[rp];
            if(pi<0||pi>=mesh.boundaryMesh().size())return fail("physical-patch-layout-omitted-live-patch");
            if(coupledSDKPatch(pi))continue; // coupled role and size remain the original literal contract
            const label f=bindings.face[nf];
            if(f<mesh.nInternalFaces()||f>=mesh.nFaces()||inverse[f]!=nf
                ||mesh.boundaryMesh().whichPatch(f)!=pi)
                return fail("physical-patch-layout-native-to-SDK-face");
            // lowerWriterNumeric has already rewritten native.owner into SDK
            // compact cells. Only inverseCells indexes the live native inventory.
            const label originalOwner=o>=0&&o<mesh.nCells()?inverseCells[o]:-1;
            if(o<0||o>=mesh.nCells()||o!=mesh.faceOwner()[f]||originalOwner<0
                ||std::size_t(originalOwner)>=native.cellAlive.size()||!native.cellAlive[originalOwner]
                ||std::size_t(originalOwner)>=bindings.cell.size()||bindings.cell[originalOwner]!=o)
                return fail("physical-patch-layout-native-owner");
            const auto& patch=mesh.boundaryMesh()[pi];
            if(patch.name()!=expectedNames[pi]||patch.type()!=expectedTypes[pi])
                return fail("physical-patch-layout-static-name-type");
            if(count[pi]==std::numeric_limits<label>::max())return fail("physical-patch-layout-count-overflow");
            ++count[pi];
        }
        for(label pi=0;pi<mesh.boundaryMesh().size();++pi)
        {
            if(!work())return fail("physical-patch-layout-clock");
            if(coupledSDKPatch(pi))continue;
            const auto& patch=mesh.boundaryMesh()[pi];
            if(patch.name()!=expectedNames[pi]||patch.type()!=expectedTypes[pi])
                return fail("physical-patch-layout-static-name-type");
            for(label f=patch.start();f<patch.start()+patch.size();++f)
            {
                if((f&1023)==0&&!work())return fail("physical-patch-layout-clock");
                const label nf=inverse[f];
                if(nf<0||std::size_t(nf)>=native.faces.size()||!native.faceAlive[nf]
                    ||bindings.face[nf]!=f||native.neighbour[nf]>=0)
                    return fail("physical-patch-layout-SDK-to-native-face");
                const label rp=native.patch[nf],o=native.owner[nf];
                if(rp<0||std::size_t(rp)>=std::size_t(patchOf.size())||patchOf[rp]!=pi
                    ||o<0||o>=mesh.nCells()||o!=mesh.faceOwner()[f])
                    return fail("physical-patch-layout-reverse-role-owner");
                const label originalOwner=inverseCells[o];
                if(originalOwner<0||std::size_t(originalOwner)>=native.cellAlive.size()||!native.cellAlive[originalOwner]
                    ||std::size_t(originalOwner)>=bindings.cell.size()||bindings.cell[originalOwner]!=o)
                    return fail("physical-patch-layout-reverse-native-owner");
                ++covered[pi];
            }
            if(count[pi]!=covered[pi]||count[pi]!=patch.size())
                return fail("physical-patch-layout-cardinality");
        }
        Counts result;result.nativeMesh=&native;result.sdkMesh=&mesh;
        result.faceBinding=bindings.face.data();result.sdkView=sdkView;
        for(label rp=0;rp<label(patchOf.size());++rp)
        {
            const label pi=patchOf[rp];if(pi<0)continue;
            if(pi>=mesh.boundaryMesh().size())return fail("physical-patch-layout-raw-map");
            if(coupledSDKPatch(pi))continue;
            Layout layout;layout.sdkPatch=pi;layout.count=count[pi];
            layout.name=expectedNames[pi];layout.type=expectedTypes[pi];result.raw.emplace(rp,std::move(layout));
        }
        if(!work())return fail("physical-patch-layout-clock");
        result.complete=true;out=std::move(result);return true;
    }catch(...){return fail("physical-patch-layout-exception");}
}
}}
#endif
