#ifndef dcCartPointRepairSourceContract_H
#define dcCartPointRepairSourceContract_H

#include "dictionary.H"
#include "entry.H"
#include "ITstream.H"
#include "OStringStream.H"
#include "Pstream.H"
#include "IOstreams.H"

namespace Foam
{
namespace dcCartPointRepairSourceContract
{
// Runner proof of locked raw bytes/resolved actual STL roles, not a native
// re-hash of CFDRUN JSON. Only the globally absent bulk/material scope is
// supported. Native installed-mesh scope checks remain independent.
struct LocalContract
{
    bool valid=false;
    bool eligible=false;
    string canonical;
    string refusal;
};

inline const ITstream* primitive(const dictionary& d,const word& key)
{
    const entry* e=d.findEntry(key,keyType::LITERAL);
    return e && e->isStream() ? e->streamPtr() : nullptr;
}

inline bool lowerSha256(const string& value)
{
    if (value.size()!=64) return false;
    for (const char ch:value)
    {
        if (!((ch>='0' && ch<='9') || (ch>='a' && ch<='f'))) return false;
    }
    return true;
}

// These checks inspect tokens without extracting from a stream: malformed
// primitive type/arity never invokes lookup/get/operator>> rank-local Fatal.
inline LocalContract readLocal(const dictionary& meshDict)
{
    LocalContract result;
    const entry* block=meshDict.findEntry
        ("dcdtuniPointRepairSourceRoleContract",keyType::LITERAL);
    if (!block)
    {
        result.canonical="<missing>";
        result.refusal="missing source-role contract";
        return result;
    }
    OStringStream normalized;
    block->write(normalized);
    result.canonical=normalized.str();
    if (!block->isDict())
    {
        result.refusal="source-role contract is not a dictionary";
        return result;
    }
    const dictionary& d=block->dict();
    for (const entry& field:d)
    {
        const keyType& key=field.keyword();
        if (!key.isLiteral() || !(key=="version" || key=="eligible"
         || key=="rawSha256" || key=="rolesDigest" || key=="reasons"
         || key=="scope"))
        {
            result.refusal="unknown source-role contract field";
            return result;
        }
    }
    const ITstream* version=primitive(d,"version");
    const ITstream* eligible=primitive(d,"eligible");
    const ITstream* raw=primitive(d,"rawSha256");
    const ITstream* roles=primitive(d,"rolesDigest");
    const ITstream* reasons=primitive(d,"reasons");
    const ITstream* scope=primitive(d,"scope");
    if (!version || version->size()!=1 || !(*version)[0].isLabel()
     || (*version)[0].labelToken()!=1)
    {
        result.refusal="source-role contract requires integer version 1";
        return result;
    }
    if (!eligible || eligible->size()!=1 || !(*eligible)[0].isWord()
     || !((*eligible)[0].wordToken()=="true"
       || (*eligible)[0].wordToken()=="false"))
    {
        result.refusal="source-role contract eligible must be true or false";
        return result;
    }
    if (!scope || scope->size()!=1 || !(*scope)[0].isWord()
     || (*scope)[0].wordToken()!="globallyNoBulkSourcesAndNoMaterialDomains")
    {
        result.refusal="unsupported source-role contract scope";
        return result;
    }
    if (!raw || raw->size()!=1 || !(*raw)[0].isString()
     || !lowerSha256((*raw)[0].stringToken())
     || !roles || roles->size()!=1 || !(*roles)[0].isString()
     || !lowerSha256((*roles)[0].stringToken()))
    {
        result.refusal="source-role contract requires quoted lowercase SHA256 values";
        return result;
    }
    if (!reasons || reasons->size()<2
     || !(*reasons)[0].isPunctuation(token::BEGIN_LIST)
     || !(*reasons)[reasons->size()-1].isPunctuation(token::END_LIST))
    {
        result.refusal="source-role contract reasons must be a string/word list";
        return result;
    }
    for (label i=1;i+1<reasons->size();++i)
    {
        if (!(*reasons)[i].isString() && !(*reasons)[i].isWord())
        {
            result.refusal="source-role contract reasons contains a non-text token";
            return result;
        }
    }
    result.eligible=((*eligible)[0].wordToken()=="true");
    if (result.eligible && reasons->size()!=2)
    {
        result.eligible=false;
        result.refusal="eligible source-role contract must have no refusal reasons";
        return result;
    }
    result.valid=true;
    if (!result.eligible) result.refusal="runner source-role proof is ineligible";
    return result;
}

inline bool exactRowsAgree(const List<string>& rows)
{
    if (rows.empty()) return false;
    for (label rank=1;rank<rows.size();++rank)
        if (rows[rank]!=rows[0]) return false;
    return true;
}

// Call only behind the existing all-rank-consistent point-repair enable gate.
// Every participating rank enters both collectives even if its block is
// missing, malformed, unknown, or ineligible. Mismatch refuses singleton3;
// the original mesh path and non-singleton repair remain available.
inline bool readEligible(const dictionary& meshDict)
{
    const LocalContract local=readLocal(meshDict);
    List<string> rows(Pstream::nProcs());
    rows[Pstream::myProcNo()]=local.canonical;
    if (Pstream::parRun())
    {
        Pstream::gatherList(rows);
        Pstream::scatterList(rows);
    }
    const bool agrees=exactRowsAgree(rows);
    const bool eligible=agrees && local.valid && local.eligible;
    if (!eligible && Pstream::master())
    {
        WarningInFunction
            << "singleton3 source-role contract refused: "
            << (agrees ? local.refusal : string("contract differs across ranks"))
            << "; existing mesh/repair path retained" << nl;
    }
    return eligible;
}
}
}
#endif
