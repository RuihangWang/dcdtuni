#ifndef dcCartWeightUnionSourceContract_H
#define dcCartWeightUnionSourceContract_H
#include "dcCartCutCells.H"
#include "dcCartPointRepairSourceContract.H"
#include <functional>
namespace Foam { namespace dcCartWeightUnionSourceContract {
// Cut-face handles / IO region are provenance, not STL triangle / domain IDs.
struct GlobalMaterialContract
{
    bool valid=false,eligible=false;
    string canonical,refusal;
    std::string rawSha256,rolesDigest,materialKey,materialDigest;
    std::string producerDigest,boundaryMembershipDigest;
};
using NormalOriginVerifier=std::function<bool
(
    const std::vector<dcCartCutCells::weightUnionOrigin>&,
    const GlobalMaterialContract&
)>;
inline GlobalMaterialContract readLocal(const dictionary& meshDict)
{
    using dcCartPointRepairSourceContract::primitive;
    using dcCartPointRepairSourceContract::lowerSha256;
    GlobalMaterialContract out;
    const entry* e=meshDict.findEntry("dcdtuniWeightUnionSourceContract",keyType::LITERAL);
    if (!e) { out.canonical="<missing>";out.refusal="missing global fluid material";return out; }
    OStringStream normalized;e->write(normalized);out.canonical=normalized.str();
    if (!e->isDict()) { out.refusal="fluid domain contract is not a dictionary";return out; }
    const dictionary& d=e->dict();
    for (const entry& field:d)
    {
        const keyType& key=field.keyword();
        if (!key.isLiteral() || !(key=="version" || key=="eligible" || key=="scope"
          || key=="rawSha256" || key=="rolesDigest" || key=="materialKey"
          || key=="materialDigest" || key=="producerDigest"
          || key=="boundaryMembershipDigest" || key=="ownershipPolicy" || key=="reasons"))
        { out.refusal="unknown fluid domain contract field";return out; }
    }
    const ITstream* version=primitive(d,"version");
    const ITstream* eligible=primitive(d,"eligible");
    const ITstream* scope=primitive(d,"scope");
    const ITstream* reasons=primitive(d,"reasons");
    if (!version || version->size()!=1 || !(*version)[0].isLabel() || (*version)[0].labelToken()!=1
     || !eligible || eligible->size()!=1 || !(*eligible)[0].isWord()
     || !((*eligible)[0].wordToken()=="true" || (*eligible)[0].wordToken()=="false")
     || !scope || scope->size()!=1 || !(*scope)[0].isWord()
     || (*scope)[0].wordToken()!="globalSinglePhaseAirMaterial")
    { out.refusal="unsupported fluid domain contract schema";return out; }
    auto quotedSha=[&](const word& key,std::string& value)
    {
        const ITstream* row=primitive(d,key);
        if (!row || row->size()!=1 || !(*row)[0].isString()
         || !lowerSha256((*row)[0].stringToken())) return false;
        value=(*row)[0].stringToken();return true;
    };
    if (!quotedSha("rawSha256",out.rawSha256) || !quotedSha("rolesDigest",out.rolesDigest)
     || !quotedSha("materialDigest",out.materialDigest)
     || !quotedSha("producerDigest",out.producerDigest)
     || !quotedSha("boundaryMembershipDigest",out.boundaryMembershipDigest))
    { out.refusal="invalid fluid domain digest";return out; }
    const ITstream* uid=primitive(d,"materialKey");
    if (!uid || uid->size()!=1 || !(*uid)[0].isString())
    { out.refusal="material key must be quoted";return out; }
    out.materialKey=(*uid)[0].stringToken();
    for (const char c:out.materialKey)
        if (!((c>='a'&&c<='z') || (c>='A'&&c<='Z') || (c>='0'&&c<='9')
          || c=='_' || c=='.' || c==':' || c=='/' || c=='-'))
        { out.refusal="invalid explicit material key";return out; }
    if (!reasons || reasons->size()<2
     || !(*reasons)[0].isPunctuation(token::BEGIN_LIST)
     || !(*reasons)[reasons->size()-1].isPunctuation(token::END_LIST))
    { out.refusal="domain reasons must be a text list";return out; }
    for (label i=1;i+1<reasons->size();++i)
        if (!(*reasons)[i].isWord() && !(*reasons)[i].isString())
        { out.refusal="invalid domain reason token";return out; }
    const ITstream* policy=primitive(d,"ownershipPolicy");
    if (!policy || policy->size()!=1 || !(*policy)[0].isWord()
     || (*policy)[0].wordToken()!="adjacentFinalCarvedFluidCells")
    { out.refusal="unsupported native fluid ownership policy";return out; }
    out.eligible=((*eligible)[0].wordToken()=="true");
    if (out.eligible && (out.materialKey.empty() || out.materialKey.size()>200 || reasons->size()!=2))
    { out.eligible=false;out.refusal="eligible domain requires material key and empty refusal list";return out; }
    out.valid=true;
    if (!out.eligible) out.refusal="runner material proof is ineligible";
    return out;
}
// No candidate MPI. Default-empty native verifier refuses even an eligible
// declaration. Both complete dictionaries participate in one fixed collective.
inline dcCartCutCells::weightUnionAuthority readAuthority
(
    const dictionary& meshDict,
    const NormalOriginVerifier& nativeOwnershipVerifier,
    const std::function<scalar()>& actualRemainingSeconds,
    const std::function<dcCartCutCells::weightUnionOrigin(label)>& normalOriginProducer
)
{
    using namespace dcCartPointRepairSourceContract;
    dcCartCutCells::weightUnionAuthority authority;
    const LocalContract necessary=dcCartPointRepairSourceContract::readLocal(meshDict);
    const GlobalMaterialContract domain=dcCartWeightUnionSourceContract::readLocal(meshDict);
    List<string> rows(Pstream::nProcs());
    rows[Pstream::myProcNo()]=necessary.canonical+"\n"+domain.canonical;
    if (Pstream::parRun()) { Pstream::gatherList(rows);Pstream::scatterList(rows); }
    bool approved=exactRowsAgree(rows) && necessary.valid && necessary.eligible
        && domain.valid && domain.eligible && bool(nativeOwnershipVerifier) && bool(actualRemainingSeconds) && bool(normalOriginProducer);
    if (approved)
    {
        const dictionary& absence=meshDict.subDict("dcdtuniPointRepairSourceRoleContract");
        const ITstream* raw=primitive(absence,"rawSha256");
        const ITstream* roles=primitive(absence,"rolesDigest");
        approved=raw && roles
            && std::string((*raw)[0].stringToken())==domain.rawSha256
            && std::string((*roles)[0].stringToken())==domain.rolesDigest;
    }
    if (!approved)
    {
        if (Pstream::master())
            WarningInFunction << "cross-parent W union refused: missing/stale material or normal parent ownership; old path retained" << nl;
        return authority;
    }
    authority.globallyNoMaterialNoBulk=true;
    authority.rawSha256=domain.rawSha256;authority.rolesDigest=domain.rolesDigest;
    authority.homogeneousFluidKey=domain.materialKey;
    authority.remainingSeconds=actualRemainingSeconds;
    authority.normalOriginFromSnapshot=normalOriginProducer;
    authority.homogeneousOrigins=[nativeOwnershipVerifier,domain]
        (const std::vector<dcCartCutCells::weightUnionOrigin>& origins)
    {
        if (origins.size()!=2 || origins[0].nativeParent==origins[1].nativeParent) return false;
        return nativeOwnershipVerifier(origins,domain);
    };
    return authority;
}
} }
#endif
