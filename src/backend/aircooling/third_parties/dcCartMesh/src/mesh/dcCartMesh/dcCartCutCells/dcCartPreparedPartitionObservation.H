#ifndef dcCartPreparedPartitionObservation_H
#define dcCartPreparedPartitionObservation_H
// Bounded observation of the actual input. Never a geometry/material authority.
#include "../dcCartSurfaces/dcCartPreparedFacetPartition.H"
#include <chrono>
#include <cstdio>
#include <iomanip>
#include <streambuf>
#include <sstream>
#include <cstdlib>
#include <unistd.h>
namespace dcCartPreparedPartitionObservation
{
namespace D=dcCartPreparedFacetPartition;
struct Budget { std::size_t entries=0,bytes=0; };
inline Budget& budget(){static thread_local Budget value;return value;}
class Buffer:public std::streambuf
{
    std::string data_;std::size_t limit_;
protected:
    std::streamsize xsputn(const char* s,std::streamsize n) override
    {
        if(n<0||std::size_t(n)>limit_-data_.size())return 0;
        data_.append(s,std::size_t(n));return n;
    }
    int_type overflow(int_type c) override
    {
        if(traits_type::eq_int_type(c,traits_type::eof()))return traits_type::not_eof(c);
        if(data_.size()==limit_)return traits_type::eof();
        data_.push_back(traits_type::to_char_type(c));return c;
    }
public:
    explicit Buffer(std::size_t limit):limit_(limit){}
    const std::string& data()const{return data_;}
};
inline void string(std::ostream& o,const std::string& s)
{
    if(s.size()>16384)throw std::runtime_error("string-cap");
    static const char hex[]="0123456789abcdef";
    o.put('"');for(unsigned char c:s)
    {
        if(c=='"'||c=='\\'){o.put('\\');o.put(char(c));}
        else if(c<32){o<<"\\u00";o.put(hex[c>>4]);o.put(hex[c&15]);}
        else o.put(char(c));
    }o.put('"');
}
inline void point(std::ostream& o,const D::Point& p)
{
    static const char hex[]="0123456789abcdef";o.put('[');
    for(unsigned a=0;a<3;++a)
    {
        if(a)o.put(',');std::uint64_t bits=0;std::memcpy(&bits,&p[a],8);o.put('"');
        for(int shift=60;shift>=0;shift-=4)o.put(hex[(bits>>shift)&15]);o.put('"');
    }o.put(']');
}
inline void checkInputStream(std::ostream& o,const std::function<bool()>& available)
{
    if(!o)throw std::runtime_error("byte-cap");
    if(!available||!available())throw std::runtime_error("original-clock");
}
// The old Trial input serializer, shared literally by the failure-only writer.
// Callback presence is observation; no callback or lease is replayed or granted.
inline void writeInput(std::ostream& o,const D::Input& in,const std::function<bool()>& available)
{
    if(in.cell.faces.size()>2048||in.cell.pointSides.size()>8192||in.cell.exactPoints.size()>8192
       ||in.window.facets.size()>2048)throw std::runtime_error("input-count-cap");
    o<<"{\"event\":\"input\",\"complete\":true,\"floatEncoding\":\"binary64-bits-hex\",\"cell\":{\"id\":"<<in.cell.id<<",\"faces\":[";
    std::size_t count=0,totalPoints=0;
    for(const auto& f:in.cell.faces)
    {
        checkInputStream(o,available);if(count++)o<<',';totalPoints+=f.points.size();
        if(f.points.size()!=f.pointIds.size()||totalPoints>8192)throw std::runtime_error("face-point-cap-or-shape");
        o<<"{\"id\":"<<f.id<<",\"owner\":"<<f.owner<<",\"neighbour\":"<<f.neighbour<<",\"patch\":"<<f.patch<<",\"zone\":"<<f.zone<<",\"pointIds\":[";
        for(std::size_t j=0;j<f.pointIds.size();++j){if(j)o<<',';o<<f.pointIds[j];}
        o<<"],\"points\":[";for(std::size_t j=0;j<f.points.size();++j){if(j)o<<',';point(o,f.points[j]);}o<<"]}";
    }
    o<<"],\"pointSides\":[";count=0;for(const auto& p:in.cell.pointSides){checkInputStream(o,available);if(count++)o<<',';o<<'['<<p.first<<','<<p.second<<']';}
    o<<"],\"exactPoints\":[";count=0;
    for(const auto& p:in.cell.exactPoints)
    {
        checkInputStream(o,available);if(count++)o<<',';const auto& x=p.second;
        o<<"{\"id\":"<<p.first<<",\"storedPoint\":";point(o,x.storedPoint);
        o<<",\"surface\":"<<x.surface<<",\"shell\":"<<x.shell<<",\"geometryEpoch\":"<<x.geometryEpoch<<",\"membershipEpoch\":"<<x.membershipEpoch<<",\"exactPoint\":[";
        for(unsigned a=0;a<3;++a){checkInputStream(o,available);if(a)o<<',';o<<'[';string(o,x.exactPoint[a].numerator().str());o<<',';string(o,x.exactPoint[a].denominator().str());o<<']';}o<<"]}";
    }
    o<<"]},\"window\":{\"surface\":"<<in.window.surface<<",\"shell\":"<<in.window.shell
     <<",\"geometryEpoch\":"<<in.window.geometryEpoch<<",\"membershipEpoch\":"<<in.window.membershipEpoch
     <<",\"complete\":"<<(in.window.complete?"true":"false")<<",\"requestedMin\":";point(o,in.window.requestedMin);
    o<<",\"requestedMax\":";point(o,in.window.requestedMax);o<<",\"facets\":[";count=0;
    for(const auto& f:in.window.facets)
    {
        checkInputStream(o,available);if(count++)o<<',';
        o<<"{\"surface\":"<<f.surface<<",\"shell\":"<<f.shell<<",\"triangle\":"<<f.triangle
         <<",\"localRegion\":"<<f.localRegion<<",\"globalRegion\":"<<f.globalRegion
         <<",\"geometryEpoch\":"<<f.geometryEpoch<<",\"membershipEpoch\":"<<f.membershipEpoch<<",\"pointIds\":[";
        for(unsigned j=0;j<3;++j){if(j)o<<',';o<<f.pointIds[j];}o<<"],\"p\":[";
        for(unsigned j=0;j<3;++j){if(j)o<<',';point(o,f.p[j]);}o<<"],\"normal\":";point(o,f.normal);
        o<<",\"sourceObjectUid\":";string(o,f.sourceObjectUid);o<<",\"sourceFaceUid\":";string(o,f.sourceFaceUid);
        o<<",\"representation\":";string(o,f.representation);o<<'}';
    }
    o<<"]},\"limits\":{\"facets\":"<<in.limits.facets<<",\"points\":"<<in.limits.points
     <<",\"facesPerPart\":"<<in.limits.facesPerPart<<",\"parts\":"<<in.limits.parts<<",\"ring\":"<<in.limits.ring
     <<",\"steps\":"<<in.limits.steps<<"},\"mergeAdjacentSectors\":"<<(in.mergeAdjacentSectors?"true":"false")
     <<",\"callbackPresenceOnly\":{\"available\":"<<(in.available?"true":"false")<<",\"solidMembership\":"<<(in.solidMembership?"true":"false")
     <<",\"materialClassification\":"<<(in.materialClassification?"true":"false")<<",\"leaseHeld\":"<<(in.leaseHeld?"true":"false")<<"}}";
}
class Trial
{
    using Clock=std::chrono::steady_clock;
    std::FILE* out_=nullptr;const std::function<bool()>& available_;
    Clock::time_point start_;bool active_=false;std::size_t events_=0;
    static constexpr std::size_t recordCap=16*1024*1024,totalCap=64*1024*1024;
    void check(std::ostream& o)
    {
        if(!o)throw std::runtime_error("byte-cap");
        if(!available_||!available_())throw std::runtime_error("original-clock");
    }
    void emit(const std::string& row)
    {
        auto& b=budget();if(!active_||row.size()+1>totalCap-b.bytes){active_=false;return;}
        b.bytes+=row.size()+1;
        if(std::fwrite(row.data(),1,row.size(),out_)!=row.size()
           ||std::fputc('\n',out_)==EOF||std::fflush(out_)!=0)active_=false;
    }
public:
    Trial(bool enabled,int rank,D::Id shell,D::Id seed,const std::function<bool()>& available) noexcept
    :available_(available)
    {
        if(!enabled)return;
        try
        {
            auto& b=budget();if(b.entries>=16||b.bytes>=totalCap||!available_||!available_())return;
            const auto sequence=b.entries++;start_=Clock::now();
            // Exclusive create preserves a previous run's observation.
            const std::string path="prepared-partition-rank"+std::to_string(rank)+"-sequence"+std::to_string(sequence)+".jsonl";
            out_=std::fopen(path.c_str(),"wx");
            active_=out_!=nullptr;std::ostringstream s;s.imbue(std::locale::classic());
            s<<"{\"schema\":\"prepared-input-journal/v1\",\"event\":\"begin\",\"rank\":"<<rank
             <<",\"shell\":"<<shell<<",\"seed\":"<<seed<<",\"sequence\":"<<sequence
             <<",\"diagnosticOnly\":true,\"grantsMaterialAuthority\":false}";emit(s.str());
        }catch(...){active_=false;}
    }
    Trial(const Trial&)=delete;
    Trial& operator=(const Trial&)=delete;
    ~Trial() noexcept{if(out_)std::fclose(out_);}
    void phase(const char* name,const std::string& reason=std::string()) noexcept
    {
        if(!active_)return;
        try
        {
            if(++events_>24){active_=false;return;}std::ostringstream s;s.imbue(std::locale::classic());
            s<<"{\"event\":\"phase\",\"name\":";string(s,name);
            s<<",\"elapsedSeconds\":"<<std::setprecision(17)<<std::chrono::duration<double>(Clock::now()-start_).count();
            s<<",\"reason\":";string(s,reason);s<<'}';emit(s.str());
        }catch(...){active_=false;}
    }
    void input(const D::Input& in) noexcept
    {
        if(!active_)return;
        try
        {
            Buffer buffer(recordCap);std::ostream o(&buffer);o.imbue(std::locale::classic());o.exceptions(std::ios::badbit|std::ios::failbit);
            writeInput(o,in,available_);
            check(o);emit(buffer.data());phase("input-recorded");
        }
        catch(const std::exception& e){phase("input-incomplete",e.what());}
        catch(...){phase("input-incomplete","exception");}
    }
};

// Independent, default-OFF observation after a genuine D refusal.
// Does not change the normal 16-entry Trial budget or any D/Install authority.
inline void failedInput
(
    int rank,D::Id shell,D::Id seed,const D::Input& in,
    const std::string& reason,const std::function<bool()>& available
) noexcept
{
    const char* option=std::getenv("DCCART_PREPARED_FAILURE_INPUT");
    if(!option||std::strcmp(option,"1")!=0||reason!="no-material-kernel-core")return;
    // Current mesher invokes prepared D sequentially; this is process-local.
    static Budget failureBudget;
    constexpr std::size_t records=2,recordBytes=2*1024*1024,processBytes=8*1024*1024;
    std::FILE* file=nullptr;bool created=false;std::string path,temporary;
    const char* incompleteReason=nullptr;
    auto incomplete=[&](const char* why) noexcept
    {
        // Bounded diagnostic status only; never hides the actual D refusal.
        std::fprintf(stderr,"Prepared failed input observation incomplete rank %d shell %lld seed %lld reason %.160s\n",
            rank,static_cast<long long>(shell),static_cast<long long>(seed),why);
    };
    try
    {
        if(failureBudget.entries>=records||failureBudget.bytes>=processBytes
           ||!available||!available())return;
        const auto sequence=failureBudget.entries++;
        Buffer buffer(recordBytes);std::ostream out(&buffer);
        out.imbue(std::locale::classic());out.exceptions(std::ios::badbit|std::ios::failbit);
        out<<"{\"schema\":\"prepared-failed-input/v1\",\"event\":\"failure\",\"rank\":"<<rank
           <<",\"shell\":"<<shell<<",\"seed\":"<<seed<<",\"sequence\":"<<sequence
           <<",\"reason\":";string(out,reason);
        out<<",\"diagnosticOnly\":true,\"grantsMaterialAuthority\":false,\"grantsHostAuthority\":false}\n";
        writeInput(out,in,available);
        out.put('\n');checkInputStream(out,available);
        const std::string& row=buffer.data();
        if(row.size()>recordBytes||row.size()>processBytes-failureBudget.bytes)
        {incomplete("failure-byte-cap");return;}
        // Charge attempted output too; an I/O failure cannot free the cap.
        failureBudget.bytes+=row.size();
        path="prepared-failed-input-rank"+std::to_string(rank)+"-shell"+std::to_string(shell)
            +"-seed"+std::to_string(seed)+"-sequence"+std::to_string(sequence)+".jsonl";
        temporary=path+".tmp";
        file=std::fopen(temporary.c_str(),"wx");
        if(!file){incomplete("exclusive-temp-open");return;}
        created=true;
        const bool written=std::fwrite(row.data(),1,row.size(),file)==row.size()
            &&std::fflush(file)==0;
        const int closeResult=std::fclose(file);file=nullptr;
        // Publish a complete observation exclusively; never overwrite a receipt.
        // The diagnostic remains subordinate to the existing work clock.
        if(written&&closeResult==0&&available()&&::link(temporary.c_str(),path.c_str())==0)
        {std::remove(temporary.c_str());created=false;}
        else incompleteReason="write-close-clock-or-exclusive-publish";
    }
    catch(const std::exception& error){incomplete(error.what());}
    catch(...){incomplete("exception");}
    if(file)std::fclose(file);
    if(created)std::remove(temporary.c_str());
    if(incompleteReason)incomplete(incompleteReason);
}
}
#endif
