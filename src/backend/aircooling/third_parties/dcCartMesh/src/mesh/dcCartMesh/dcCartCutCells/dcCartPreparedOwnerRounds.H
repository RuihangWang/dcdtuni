#ifndef dcCartPreparedOwnerRounds_H
#define dcCartPreparedOwnerRounds_H
#include "Pstream.H"
#include <algorithm>
#include <cstdint>
#include <functional>
#include <limits>
#include <string>
#include <vector>

namespace dcCartPreparedOwnerRounds
{
using Words=std::vector<std::uint64_t>;
struct Frame { bool ready=true; Words words; std::string reason; };
struct Entry { int rank=-1; Words words; };
// Framing is transport only. It cannot grant source/material/star permission.
inline bool frames(const Frame& local,const std::function<bool()>& available,
    std::vector<Frame>& result,std::string& refusal,std::size_t limit)
{
    using namespace Foam;
    Frame mine=local;
    if(!available||!available())
    {mine.ready=false;mine.words.clear();mine.reason="owner-round-clock";}
    if(mine.words.size()>limit/sizeof(std::uint64_t))
    {mine.ready=false;mine.words.clear();mine.reason="owner-round-payload-cap";}
    if(mine.reason.size()>256)mine.reason.resize(256);
    Words packed{mine.ready?1u:0u,std::uint64_t(mine.reason.size())};
    for(unsigned char ch:mine.reason)packed.push_back(ch);
    packed.insert(packed.end(),mine.words.begin(),mine.words.end());
    std::vector<Words> all;
    if(!UPstream::parRun())all.push_back(std::move(packed));
    else
    {
        List<List<std::uint64_t>> gathered(UPstream::nProcs());
        auto& message=gathered[UPstream::myProcNo()];
        message.resize(label(packed.size()));
        forAll(message,i)message[i]=packed[std::size_t(i)];
        // Even a rank whose clock/precondition failed participates in the vote.
        Pstream::allGatherList(gathered);
        all.resize(gathered.size());
        forAll(gathered,i)all[std::size_t(i)].assign(gathered[i].begin(),gathered[i].end());
    }
    std::vector<Frame> staged;
    std::size_t payload=0;
    for(std::size_t rank=0;rank<all.size();++rank)
    {
        const auto& words=all[rank];
        if(words.size()<2||words[0]>1||words[1]>256||words[1]>words.size()-2)
        {refusal="owner-round-invalid-frame";return false;}
        const std::size_t prefix=2+std::size_t(words[1]);
        const std::size_t n=words.size()-prefix;
        if(n>limit/sizeof(std::uint64_t)-payload)
        {refusal="owner-round-total-payload-cap";return false;}
        payload+=n;
        Frame item;item.ready=words[0]!=0;
        for(std::size_t j=2;j<prefix;++j)
        {
            if(words[j]>255){refusal="owner-round-invalid-reason";return false;}
            item.reason.push_back(char(words[j]));
        }
        item.words.assign(words.begin()+prefix,words.end());
        if(!item.ready)
        {
            refusal="rank "+std::to_string(rank)+": "+
                (item.reason.empty()?"owner-round-refused":item.reason);
            return false;
        }
        staged.push_back(std::move(item));
    }
    result.swap(staged);
    return true;
}
inline bool vote(bool ready,const std::string& reason,
    const std::function<bool()>& available,std::string& refusal)
{
    Frame mine;mine.ready=ready;mine.reason=reason;
    std::vector<Frame> all;
    return frames(mine,available,all,refusal,4096);
}
inline bool broadcast(int owner,const Frame& mine,const std::function<bool()>& available,
    Frame& result,std::string& refusal,std::size_t limit)
{
    using namespace Foam;
    const int ranks=UPstream::parRun()?UPstream::nProcs():1;
    Frame local=mine;
    if(owner<0||owner>=ranks)
    {local.ready=false;local.words.clear();local.reason="owner-round-invalid-owner";}
    if(UPstream::myProcNo()!=owner&&!local.words.empty())
    {local.ready=false;local.words.clear();local.reason="owner-round-nonowner-payload";}
    std::vector<Frame> all;
    if(!frames(local,available,all,refusal,limit))return false;
    result=std::move(all[std::size_t(owner)]);
    return true;
}
inline bool requests(const std::vector<Words>& local,
    const std::function<bool()>& available,std::vector<Entry>& result,
    std::string& refusal,std::size_t limit)
{
    Frame mine;mine.words.push_back(local.size());
    for(const auto& item:local)
    {
        if(item.size()>=limit/sizeof(std::uint64_t)||
           mine.words.size()>limit/sizeof(std::uint64_t)-item.size()-1)
        {mine.ready=false;mine.words.clear();mine.reason="owner-round-request-cap";break;}
        mine.words.push_back(item.size());
        mine.words.insert(mine.words.end(),item.begin(),item.end());
    }
    std::vector<Frame> all;
    if(!frames(mine,available,all,refusal,limit))return false;
    std::vector<Entry> staged;
    for(std::size_t rank=0;rank<all.size();++rank)
    {
        const auto& payload=all[rank].words;
        if(payload.empty()){refusal="owner-round-request-frame";return false;}
        std::size_t at=1;
        const std::uint64_t count=payload[0];
        if(count>payload.size()){refusal="owner-round-request-count";return false;}
        for(std::uint64_t i=0;i<count;++i)
        {
            if(at>=payload.size()){refusal="owner-round-request-size";return false;}
            const auto n=payload[at++];
            if(n>payload.size()-at){refusal="owner-round-request-size";return false;}
            Entry item;item.rank=int(rank);
            item.words.assign(payload.begin()+at,payload.begin()+at+std::size_t(n));
            at+=std::size_t(n);staged.push_back(std::move(item));
        }
        if(at!=payload.size()){refusal="owner-round-request-tail";return false;}
    }
    result.swap(staged);
    return true;
}
} // namespace dcCartPreparedOwnerRounds
#endif
