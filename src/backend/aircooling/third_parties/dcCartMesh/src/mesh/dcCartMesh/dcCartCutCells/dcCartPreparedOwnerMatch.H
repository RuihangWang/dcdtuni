#ifndef dcCartPreparedOwnerMatch_H
#define dcCartPreparedOwnerMatch_H
#include "dcCartPreparedOwnerWire.H"
#include "dcCartPreparedStarMatch.H"
#include "dcCartPreparedFacetContext.H"
#include <set>
#include <limits>
#include <tuple>
#include <sstream>
#include <fcntl.h>
#include <unistd.h>
namespace dcCartPreparedOwnerMatch
{
namespace W=::dcCartPreparedOwnerWire;
namespace D=::dcCartPreparedFacetPartition;
namespace C=::dcCartPreparedFacetContext;
inline bool sameBits(double a,double b)
{std::uint64_t x=0,y=0;std::memcpy(&x,&a,8);std::memcpy(&y,&b,8);return x==y;}
inline bool samePoint(const D::Point& a,const D::Point& b)
{for(unsigned i=0;i<3;++i)if(!sameBits(a[i],b[i]))return false;return true;}
// This is equality of the authentic native window, not a geometric permission.
inline bool windowEqual(const D::Window& a,const D::Window& b)
{
    if(a.surface!=b.surface||a.shell!=b.shell||a.geometryEpoch!=b.geometryEpoch
      ||a.membershipEpoch!=b.membershipEpoch||a.complete!=b.complete
      ||!samePoint(a.requestedMin,b.requestedMin)||!samePoint(a.requestedMax,b.requestedMax)
      ||a.facets.size()!=b.facets.size())return false;
    // Native catalogue order is irrelevant; identities and all stored data are exact.
    std::map<D::Id,const D::NativeFacet*> other;
    for(const auto& f:b.facets)if(!other.emplace(f.triangle,&f).second)return false;
    for(const auto& f:a.facets)
    {
        const auto it=other.find(f.triangle);if(it==other.end())return false;
        const auto& g=*it->second;
        if(f.surface!=g.surface||f.shell!=g.shell||f.localRegion!=g.localRegion
          ||f.globalRegion!=g.globalRegion||f.pointIds!=g.pointIds
          ||f.geometryEpoch!=g.geometryEpoch||f.membershipEpoch!=g.membershipEpoch
          ||f.sourceObjectUid!=g.sourceObjectUid||f.sourceFaceUid!=g.sourceFaceUid
          ||f.representation!=g.representation||!samePoint(f.normal,g.normal))return false;
        for(unsigned j=0;j<3;++j)if(!samePoint(f.p[j],g.p[j]))return false;
        other.erase(it);
    }
    return other.empty();
}
inline bool fail(std::string* why,const char* text)
{if(why)*why=text;return false;}
// Only the complete Context capture grants completeness. Outer endpoints retain
// their raw cell metadata, but never masquerade as complete affected cells.
// Extraction-local point-footprint breadcrumbs are deliberately not transport
// identities; each holder proves them again through its actual local callback.
template<class Mesh>
bool collectStar(const Mesh& m,const std::vector<signed char>& sides,
    const std::map<D::Id,D::OriginalExactPoint>& construction,const C::FullStar& native,
    const std::function<bool()>& available,W::FullStar& out,std::string* why=nullptr)
{
    try
    {
        if(!available||!available()||!native.complete||native.seed<0
          ||!native.cells.count(native.seed))return fail(why,"wire-incomplete-capture");
        W::FullStar staged;std::set<int> cells,faces,points;
        for(const auto& kv:native.cells)
        {
            if(!available())return fail(why,"wire-clock");
            if(kv.first<0||kv.first>=m.nCells||kv.first!=kv.second.id)
                return fail(why,"wire-invalid-cell");
            cells.insert(int(kv.first));
            for(const auto& f:kv.second.faces)
            {
                if(f.id<0||std::size_t(f.id)>=m.faces.size()||!m.faceAlive[f.id])
                    return fail(why,"wire-invalid-face");
                faces.insert(int(f.id));
            }
        }
        for(int f:faces)
        {
            if(!available())return fail(why,"wire-clock");
            if(m.owner[f]<0||m.owner[f]>=m.nCells||m.neighbour[f]<-1
              ||m.neighbour[f]>=m.nCells)return fail(why,"wire-invalid-incidence");
            cells.insert(m.owner[f]);if(m.neighbour[f]>=0)cells.insert(m.neighbour[f]);
            W::Face dst;dst.id=f;dst.owner=m.owner[f];dst.neighbour=m.neighbour[f];
            dst.patch=m.patch[f];dst.zone=m.zone[f];dst.alive=m.faceAlive[f];
            for(int p:m.faces[f]){points.insert(p);dst.points.push_back(p);}
            staged.faces.push_back(std::move(dst));
        }
        if(cells.size()>8192||faces.size()>65536||points.size()>65536)
            return fail(why,"wire-capacity");
        for(int c:cells)
        {
            if(!available())return fail(why,"wire-clock");
            if(c<0||c>=m.nCells||std::size_t(c)>=m.cellRank.size()
              ||std::size_t(c)>=m.cellLevel.size()||std::size_t(c)>=m.cellLeaf.size()
              ||std::size_t(c)>=m.cellPiece.size()||!m.cellAlive[c])
                return fail(why,"wire-missing-cell-metadata");
            W::Cell dst;dst.id=c;dst.ownerRank=m.cellRank[c];dst.level=m.cellLevel[c];
            dst.leaf=m.cellLeaf[c];dst.piece=m.cellPiece[c];dst.alive=m.cellAlive[c];
            if(dst.ownerRank<0||dst.level<0)return fail(why,"wire-invalid-family");
            dst.origAlive=m.cellOrigAlive[c];dst.complete=native.cells.count(c);
            dst.refVolume=m.cellRefVol[c];const auto& p=m.cellRefCentre[c];
            dst.refCentre={{p[0],p[1],p[2]}};
            for(const auto& f:staged.faces)if(f.owner==c||f.neighbour==c)dst.faces.push_back(f.id);
            staged.cells.push_back(std::move(dst));
        }
        for(int p:points)
        {
            if(!available())return fail(why,"wire-clock");
            if(p<0||std::size_t(p)>=m.points.size()||std::size_t(p)>=sides.size())
                return fail(why,"wire-invalid-point");
            W::PointRecord dst;dst.id=p;const auto& x=m.points[p];dst.p={{x[0],x[1],x[2]}};
            if(!D::detail::finite(dst.p))return fail(why,"wire-nonfinite-point");
            dst.side=int(sides[p]);
            if(!m.preparedExactPoints.empty())
            {
                if(m.preparedExactPoints.size()!=m.points.size())return fail(why,"wire-pin-size");
                dst.exactPin=bool(m.preparedExactPoints[p]);
            }
            staged.points.push_back(dst);
            const auto found=construction.find(p);
            if(found!=construction.end())
            {
                if(!samePoint(found->second.storedPoint,dst.p))return fail(why,"wire-stale-exact-point");
                staged.exactConstruction.emplace(p,found->second);
            }
        }
        if(!available())return fail(why,"wire-clock");
        out=std::move(staged);return true;
    }
    catch(const std::exception& e){if(why)*why=e.what();return false;}
    catch(...){return fail(why,"wire-snapshot-exception");}
}
inline const W::Cell* cell(const W::FullStar& star,D::Id id)
{
    const W::Cell* out=nullptr;
    for(const auto& c:star.cells)if(c.id==id){if(out)return nullptr;out=&c;}
    return out;
}
inline std::set<int> affectedOwnerRanks(const W::FullStar& star)
{
    std::set<int> out;for(const auto& c:star.cells)if(c.complete&&c.alive)out.insert(c.ownerRank);
    return out;
}

// Failure-only observation. Configuration is supplied by the real MPI caller,
// never inferred from packet ownership. The sum of all rank quotas is <=16MiB.
// Counters are process-local and are NOT reset between shells/transactions.
struct FailureDiagnosticState
{
    int rank=-1,ranks=0;bool enabled=false;std::size_t bytes=0;std::uint64_t sequence=0;
};
inline FailureDiagnosticState& failureDiagnosticState()
{static FailureDiagnosticState state;return state;}
inline void configureFailureDiagnostic(int rank,int ranks,bool enabled) noexcept
{
    auto& s=failureDiagnosticState();
    if(s.rank<0&&rank>=0&&ranks>0&&rank<ranks){s.rank=rank;s.ranks=ranks;}
    s.enabled=enabled&&s.rank==rank&&s.ranks==ranks&&rank>=0&&rank<ranks;
}
struct FailureDiagnosticRecord
{
    std::string kind,reason;D::Id seed=-1;int ownerRank=-1,level=-1;
    std::uint64_t leaf=0;std::size_t cells=0,faces=0,points=0;
    std::vector<std::uint64_t> words;
};
class FailureDiagnostic
{
    const std::function<bool()>& available_;const std::string* finalReason_;W::Identity identity_;
    std::vector<FailureDiagnosticRecord> records_;
    std::size_t reserved_=0;bool enabled_=false,success_=false,truncated_=false;
    static constexpr std::size_t totalCap_=16u*1024u*1024u;
    static constexpr std::size_t recordOverhead_=2048;
    bool live() const {return enabled_&&available_&&available_();}
    std::size_t remaining() const
    {
        const auto& s=failureDiagnosticState();
        if(!enabled_||s.ranks<=0)return 0;
        const std::size_t quota=totalCap_/std::size_t(s.ranks);
        if(s.bytes>quota||reserved_>quota-s.bytes)return 0;
        return quota-s.bytes-reserved_;
    }
    template<class Packet>
    void encoded(FailureDiagnosticRecord record,const Packet& packet)
    {
        if(!live())return;
        const auto room=remaining();
        if(room<=recordOverhead_){truncated_=true;return;}
        W::Limits limits;limits.bytes=room-recordOverhead_;limits.available=available_;
        std::string why;
        if(!W::encode(packet,record.words,&why,limits))
        {
            record.reason+=";diagnostic-encode:"+why;
            record.words.clear();
            truncated_=true;
        }
        if(record.reason.size()>512)record.reason.resize(512);
        const std::size_t charge=recordOverhead_+8*record.words.size();
        if(charge>remaining()){truncated_=true;return;}
        records_.push_back(std::move(record));reserved_+=charge;
    }
    static bool writeAll(int fd,const void* data,std::size_t bytes)
    {
        const auto* p=static_cast<const unsigned char*>(data);
        while(bytes)
        {
            const auto n=::write(fd,p,bytes);
            if(n<=0)return false;
            p+=n;bytes-=std::size_t(n);
        }
        return true;
    }
    void flush()
    {
        if(success_||!live())return;
        auto& state=failureDiagnosticState();const auto sequence=state.sequence++;
        for(std::size_t i=0;i<records_.size();++i)
        {
            if(!live())return;
            const auto& r=records_[i];
            const std::string path="prepared-owner-match-failure-rank"
              +std::to_string(state.rank)+"-sequence"+std::to_string(sequence)
              +"-record"+std::to_string(i)+".w64";
            // Never replace a prior primary record, even in a reused cwd.
            const int fd=::open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL,0600);
            if(fd<0)continue;
            const std::size_t charge=recordOverhead_+8*r.words.size();
            state.bytes+=charge; // charge attempted bytes, including partial writes
            const bool written=writeAll(fd,r.words.data(),8*r.words.size());
            const bool closed=::close(fd)==0;
            std::ostringstream line;
            line<<"Prepared owner failure packet rank "<<state.rank<<" sequence "<<sequence
              <<" record "<<i<<" kind "<<r.kind<<" seed "<<r.seed
              <<" owner "<<r.ownerRank<<" level "<<r.level<<" leaf "<<r.leaf
              <<" cells "<<r.cells<<" faces "<<r.faces<<" points "<<r.points
              <<" words "<<r.words.size()<<" written "<<(written&&closed)
              <<" truncated "<<truncated_<<" path "<<path<<" reasonHex ";
            static const char hex[]="0123456789abcdef";
            const std::string& reason=r.reason.empty()&&finalReason_?*finalReason_:r.reason;
            for(std::size_t j=0;j<reason.size()&&j<512;++j)
            {const unsigned char c=reason[j];line<<hex[c>>4]<<hex[c&15];}
            line<<'\n';const auto text=line.str();
            // One short write keeps rank/record identity intact in MPI stderr.
            if(text.size()<recordOverhead_)::write(STDERR_FILENO,text.data(),text.size());
        }
    }
public:
    explicit FailureDiagnostic(const W::Request& request,const std::function<bool()>& available,
                               const std::string* finalReason=nullptr) noexcept
    :available_(available),finalReason_(finalReason)
    {
        try
        {
            enabled_=failureDiagnosticState().enabled;
            if(!live())return;identity_=request.identity;
            FailureDiagnosticRecord r;r.kind="sender-request";r.seed=request.originalCell.id;
            r.ownerRank=request.ownerRank;r.level=request.level;r.leaf=request.leaf;
            r.cells=request.star.cells.size();r.faces=request.star.faces.size();
            r.points=request.star.points.size();encoded(std::move(r),request);
        }
        catch(...){enabled_=false;}
    }
    explicit FailureDiagnostic(const W::OwnerPacket& packet,const std::function<bool()>& available,
                               const std::string* finalReason=nullptr) noexcept
    :available_(available),finalReason_(finalReason)
    {
        try
        {
            enabled_=failureDiagnosticState().enabled;
            if(!live())return;identity_=packet.identity;
            FailureDiagnosticRecord r;r.kind="sender-owner-packet";r.seed=packet.seed;
            r.ownerRank=packet.ownerRank;const auto* c=cell(packet.star,packet.seed);
            if(c){r.level=c->level;r.leaf=c->leaf;}
            r.cells=packet.star.cells.size();r.faces=packet.star.faces.size();
            r.points=packet.star.points.size();encoded(std::move(r),packet);
        }
        catch(...){enabled_=false;}
    }
    ~FailureDiagnostic() noexcept {try{flush();}catch(...){}}
    void accept() noexcept {success_=true;}
    template<class Mesh>
    void candidate(const Mesh& m,int seed,const C::FullStar& native,const W::FullStar& wire,
                   const std::string& reason) noexcept
    {
        try
        {
            if(!live())return;
            FailureDiagnosticRecord r;r.kind="candidate-request";r.seed=seed;r.reason=reason;
            r.cells=wire.cells.size();r.faces=wire.faces.size();r.points=wire.points.size();
            if(seed<0||seed>=m.nCells||std::size_t(seed)>=m.cellRank.size()
              ||std::size_t(seed)>=m.cellLevel.size()||std::size_t(seed)>=m.cellLeaf.size())
                return;
            r.ownerRank=m.cellRank[seed];r.level=m.cellLevel[seed];r.leaf=m.cellLeaf[seed];
            W::Request local;local.senderRank=failureDiagnosticState().rank;
            local.ownerRank=r.ownerRank;local.level=r.level;local.leaf=r.leaf;local.identity=identity_;
            const auto found=native.cells.find(seed);
            if(found==native.cells.end()||wire.cells.empty())
            {
                // Empty words explicitly mean there was no complete local W star.
                const auto room=remaining();if(room<recordOverhead_){truncated_=true;return;}
                records_.push_back(std::move(r));reserved_+=recordOverhead_;return;
            }
            local.originalCell=found->second;local.star=wire;encoded(std::move(r),local);
        }
        catch(...){truncated_=true;}
    }
};

template<class Mesh>
bool family(const Mesh& m,int c,const W::Cell& reference)
{
    return c>=0&&c<m.nCells&&m.cellAlive[c]&&std::size_t(c)<m.cellRank.size()
      &&std::size_t(c)<m.cellLevel.size()&&std::size_t(c)<m.cellLeaf.size()
      &&m.cellRank[c]==reference.ownerRank&&m.cellLevel[c]==reference.level
      &&m.cellLeaf[c]==reference.leaf;
}
struct Located
{
    bool holder=false;int seed=-1;C::FullStar star;W::FullStar wire;W::Maps maps;
};
// Return success with holder=false only if this rank has no affected cell at all.
// A partially held transaction is a veto, not permission to leave a stale halo.
template<class Mesh,class CompletePointStar>
bool locate(const Mesh& m,const std::vector<signed char>& sides,
    const std::map<D::Id,D::OriginalExactPoint>& construction,const W::FullStar& sender,
    D::Id seed,CompletePointStar&& completePointStar,const std::function<bool()>& available,
    Located& out,std::string* why,const C::CaptureLimits& limits,
    FailureDiagnostic* diagnostic=nullptr)
{
    try
    {
        if(!available||!available())return fail(why,"owner-match-clock");
        const W::Cell* source=cell(sender,seed);
        if(!source||!source->complete||!source->alive||source->ownerRank<0)
            return fail(why,"owner-match-invalid-seed");
        std::vector<int> candidates;bool holds=false;
        std::set<std::tuple<int,int,std::uint64_t>> affectedFamilies;
        for(const auto& old:sender.cells)if(old.complete)
            affectedFamilies.emplace(old.ownerRank,old.level,old.leaf);
        for(int c=0;c<m.nCells;++c)
        {
            if(!available())return fail(why,"owner-match-clock");
            if(family(m,c,*source))candidates.push_back(c);
            if(!holds&&m.cellAlive[c]&&std::size_t(c)<m.cellRank.size()
              &&std::size_t(c)<m.cellLevel.size()&&std::size_t(c)<m.cellLeaf.size())
                holds=affectedFamilies.count(std::make_tuple(m.cellRank[c],m.cellLevel[c],m.cellLeaf[c]));
        }
        if(!holds){out=Located();return true;}
        if(candidates.empty())return fail(why,"owner-match-half-held");
        std::vector<std::vector<int>> cf(m.nCells);
        for(std::size_t f=0;f<m.faces.size();++f)
        {
            if(!available())return fail(why,"owner-match-clock");
            if(!m.faceAlive[f])continue;
            for(int c:{m.owner[f],m.neighbour[f]})
                if(c>=0&&c<m.nCells)cf[c].push_back(int(f));
        }
        Located chosen;unsigned matches=0;std::string last;
        for(int candidate:candidates)
        {
            if(!available())return fail(why,"owner-match-clock");
            Located trial;C::CaptureFailure failure;
            if(!C::capture(m,cf,candidate,sides,available,completePointStar,trial.star,limits,&failure))
            {
                last=failure.reason?failure.reason:"owner-match-capture";
                if(diagnostic)diagnostic->candidate(m,candidate,trial.star,trial.wire,last
                    +" id="+std::to_string(failure.id)+" observed="+std::to_string(failure.observed)
                    +" limit="+std::to_string(failure.limit));
                continue;
            }
            if(!collectStar(m,sides,construction,trial.star,available,trial.wire,&last))
            {if(diagnostic)diagnostic->candidate(m,candidate,trial.star,trial.wire,last);continue;}
            if(!dcCartPreparedStarMatch::match(sender,trial.wire,available,trial.maps,&last))
            {if(diagnostic)diagnostic->candidate(m,candidate,trial.star,trial.wire,last);continue;}
            const auto mapped=trial.maps.cells.find(seed);
            if(mapped==trial.maps.cells.end()||mapped->second!=candidate)
            {
                last="owner-match-wrong-seed";
                if(diagnostic)diagnostic->candidate(m,candidate,trial.star,trial.wire,last);
                continue;
            }
            if(diagnostic)diagnostic->candidate(m,candidate,trial.star,trial.wire,"matched-candidate");
            if(++matches!=1)return fail(why,"owner-match-ambiguous-cell");
            trial.holder=true;trial.seed=candidate;chosen=std::move(trial);
        }
        if(!matches){if(why)*why=last.empty()?"owner-match-no-complete-preimage":last;return false;}
        out=std::move(chosen);return true;
    }
    catch(const std::exception& e){if(why)*why=e.what();return false;}
    catch(...){return fail(why,"owner-match-exception");}
}
// Install compares the actual local stored ring item-for-item. The proven
// bijection permits cyclic reindexing, but never a geometry or incidence change.
// Normalize only after verifying the mapped data against the fresh local cell.
inline bool normalizePreparedInput(D::OriginalCell& mapped,const D::OriginalCell& fresh,
    const std::map<D::Id,D::OriginalExactPoint>& construction,
    const std::function<bool()>& available,std::string* why)
{
    if(!available||!available()||mapped.id!=fresh.id||mapped.faces.size()!=fresh.faces.size()
      ||mapped.pointSides!=fresh.pointSides)return fail(why,"owner-input-cell-state");
    std::map<D::Id,const D::OriginalFace*> originals;
    for(const auto& f:mapped.faces)if(!originals.emplace(f.id,&f).second)
        return fail(why,"owner-input-duplicate-face");
    std::set<D::Id> pointIds;
    for(const auto& f:fresh.faces)
    {
        if(!available())return fail(why,"owner-input-clock");
        const auto found=originals.find(f.id);
        if(found==originals.end())return fail(why,"owner-input-missing-face");
        const auto& g=*found->second;const std::size_t n=f.pointIds.size();
        if(!n||n!=g.pointIds.size()||n!=f.points.size()||n!=g.points.size()
          ||f.owner!=g.owner||f.neighbour!=g.neighbour||f.patch!=g.patch||f.zone!=g.zone)
            return fail(why,"owner-input-face-state");
        const auto first=std::find(g.pointIds.begin(),g.pointIds.end(),f.pointIds.front());
        if(first==g.pointIds.end())return fail(why,"owner-input-ring-start");
        const std::size_t offset=std::size_t(first-g.pointIds.begin());
        for(std::size_t j=0;j<n;++j)
        {
            if(!available())return fail(why,"owner-input-clock");
            if(f.pointIds[j]!=g.pointIds[(offset+j)%n]
              ||!samePoint(f.points[j],g.points[(offset+j)%n]))
                return fail(why,"owner-input-ring-state");
            pointIds.insert(f.pointIds[j]);
        }
        originals.erase(found);
    }
    std::map<D::Id,D::OriginalExactPoint> currentExact;
    for(D::Id p:pointIds)
    {
        if(!available())return fail(why,"owner-input-clock");
        const auto found=construction.find(p);
        if(found!=construction.end())currentExact.emplace(p,found->second);
    }
    if(!originals.empty()||currentExact!=mapped.exactPoints)
        return fail(why,"owner-input-construction-state");
    D::OriginalCell normalized=fresh;normalized.exactPoints=std::move(currentExact);
    if(!available())return fail(why,"owner-input-clock");
    mapped=std::move(normalized);return true;
}
struct Result
{
    bool holder=false;int seed=-1;C::FullStar star;D::Input authority;D::Output output;
    std::set<int> certifiedFaces,affectedOwnerRanks;
};
template<class Mesh,class CompletePointStar>
bool locatePreparedRequest(const Mesh& m,const std::vector<signed char>& sides,
    const std::map<D::Id,D::OriginalExactPoint>& construction,const W::Request& request,
    CompletePointStar&& completePointStar,const std::function<bool()>& available,int& seed,
    std::string* why=nullptr,const C::CaptureLimits& limits=C::CaptureLimits())
{
    FailureDiagnostic diagnostic(request,available,why);
    const auto* c=cell(request.star,request.originalCell.id);
    if(!c||c->ownerRank!=request.ownerRank||c->level!=request.level||c->leaf!=request.leaf)
        return fail(why,"request-key-mismatch");
    Located found;
    if(!locate(m,sides,construction,request.star,request.originalCell.id,
               completePointStar,available,found,why,limits,&diagnostic))return false;
    seed=found.seed;diagnostic.accept();return true;
}
template<class Mesh,class CompletePointStar>
bool matchPreparedOwnerTransaction(const Mesh& m,const std::vector<signed char>& sides,
    const std::map<D::Id,D::OriginalExactPoint>& construction,const W::OwnerPacket& packet,
    CompletePointStar&& completePointStar,const std::function<bool()>& available,Result& out,
    std::string* why=nullptr,const C::CaptureLimits& limits=C::CaptureLimits())
{
    FailureDiagnostic diagnostic(packet,available,why);
    if(packet.seed!=packet.input.cell.id||packet.seed!=packet.output.originalCell
      ||!packet.output.geometricKnown||!packet.input.window.complete
      ||packet.identity.surface!=packet.input.window.surface
      ||packet.identity.shell!=packet.input.window.shell
      ||packet.identity.geometryEpoch!=packet.input.window.geometryEpoch
      ||packet.identity.membershipEpoch!=packet.input.window.membershipEpoch)
        return fail(why,"owner-packet-identity");
    const auto* c=cell(packet.star,packet.seed);
    if(!c||c->ownerRank!=packet.ownerRank)return fail(why,"owner-packet-owner");
    Located found;
    if(!locate(m,sides,construction,packet.star,packet.seed,completePointStar,available,found,why,limits,&diagnostic))
        return false;
    Result staged;staged.affectedOwnerRanks=affectedOwnerRanks(packet.star);
    if(!found.holder){out=std::move(staged);diagnostic.accept();return true;}
    W::OwnerPacket mapped;
    W::Limits wireLimits;wireLimits.available=available;
    if(!W::remapOwner(packet,found.maps,mapped,why,wireLimits))return false;
    if(mapped.seed!=found.seed)return fail(why,"owner-remapped-seed");
    if(!normalizePreparedInput(mapped.input.cell,found.star.cells.at(found.seed),
        construction,available,why))return false;
    staged.holder=true;staged.seed=found.seed;staged.star=std::move(found.star);
    staged.authority=std::move(mapped.input);staged.output=std::move(mapped.output);
    // These certificates only bind exact old faces. The caller must first obtain
    // every affected owner's matching vote, and still apply native patch/source
    // policy, fresh lease/material callbacks and all original Install gates.
    for(const auto& f:found.maps.faces)
    {
        if(f.second<0||f.second>std::numeric_limits<int>::max())return fail(why,"owner-face-range");
        staged.certifiedFaces.insert(int(f.second));
    }
    out=std::move(staged);diagnostic.accept();return true;
}
}
#endif
