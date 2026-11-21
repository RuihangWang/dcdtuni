#ifndef dcCartPreparedOwnerWire_H
#define dcCartPreparedOwnerWire_H
#include "dcCartPreparedFacetPartition.H"
#include <cstdint>
#include <vector>
#include <map>
#include <string>
#include <functional>
#include <algorithm>
#include <iterator>
#include <set>
#include <limits>
#include <cstring>
#include <stdexcept>

namespace dcCartPreparedOwnerWire
{
namespace D=::dcCartPreparedFacetPartition;
using Id=D::Id;
struct Identity
{
    // Optional authentic outer bindings, never synthesized by this codec.
    std::string caseIdentity,inputSha,sourceCatalogueSha;
    Id surface=-1,shell=-1;
    std::uint64_t geometryEpoch=0,membershipEpoch=0;
};
struct Cell
{
    Id id=-1;int ownerRank=-1,level=-1;
    std::uint64_t leaf=0,piece=0; // piece is diagnostic split history, not identity
    bool alive=false,origAlive=false,complete=false;
    double refVolume=0;D::Point refCentre{{0,0,0}};
    std::vector<Id> faces;
};
struct Face
{
    Id id=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    bool alive=false;std::vector<Id> points;
};
struct PointRecord
{
    Id id=-1;D::Point p{{0,0,0}};int side=0;bool exactPin=false;
    std::vector<Id> footprint;
};
struct FullStar
{
    std::vector<Cell> cells;std::vector<Face> faces;std::vector<PointRecord> points;
    std::map<Id,D::OriginalExactPoint> exactConstruction;
};
struct Maps
{
    std::map<Id,Id> cells,faces,points; // sender-local -> holder-local, bijective
    std::map<Id,bool> faceReversed;    // sender stored cycle versus holder cycle
};
struct Request
{
    int senderRank=-1,ownerRank=-1,level=-1;std::uint64_t leaf=0;
    D::OriginalCell originalCell;
    FullStar star;
    Identity identity;
};
struct OwnerPacket
{
    int ownerRank=-1;std::uint64_t transaction=0;Id seed=-1;
    Identity identity;
    FullStar star;
    D::Input input; // codec preserves data; all four callbacks decode empty
    D::Output output;
};
struct Limits
{
    std::size_t bytes=64u*1024u*1024u;
    std::size_t cells=8192,faces=65536,points=65536,ring=256,events=512;
    std::size_t nodes=8192,parts=2048,facets=2048,steps=4000000,stringBytes=4096;
    std::function<bool()> available; // optional actual clock; never serialized
};
// No source, material, lease, ownership or quality permission is transmitted.
// A successful decoder/remapper only prepares a temporary representation.
// The holder must rebind authentic callbacks and validate fresh preimage,
// complete stars, actual source catalogue/window and Install before MPI vote.
inline bool encode(const Request&,std::vector<std::uint64_t>&,std::string*,const Limits& = Limits());
inline bool encode(const OwnerPacket&,std::vector<std::uint64_t>&,std::string*,const Limits& = Limits());
inline bool decode(const std::vector<std::uint64_t>&,Request&,std::string*,const Limits& = Limits());
inline bool decode(const std::vector<std::uint64_t>&,OwnerPacket&,std::string*,const Limits& = Limits());
inline bool remapOwner(const OwnerPacket&,const Maps&,OwnerPacket&,std::string*,const Limits& = Limits());

namespace detail
{
struct Refusal:std::runtime_error {explicit Refusal(const char* s):std::runtime_error(s){}};
template<class T>struct Serde;
template<class Derived>struct Archive
{
    const Limits& limits;std::size_t steps=0,allocated=0;
    explicit Archive(const Limits& l):limits(l){}
    void tick(){if(++steps>limits.steps)throw Refusal("wire-work-limit");
        if(limits.available&&!limits.available())throw Refusal("wire-clock");}
    void allocate(std::uint64_t n,std::size_t unit)
    {if(unit&&n>(limits.bytes-allocated)/unit)throw Refusal("wire-allocation-limit");allocated+=std::size_t(n)*unit;}
    template<class T>void field(T& x){Serde<T>::io(static_cast<Derived&>(*this),x);}
    void fields(){}
    template<class T,class... Rest>void fields(T& x,Rest&... xs){field(x);fields(xs...);}
    std::size_t recordLimit()const{return std::max(std::max(limits.cells,limits.faces),std::max(limits.points,limits.nodes));}
};
struct Writer:Archive<Writer>
{
    static constexpr bool reading=false;std::vector<std::uint64_t> words;
    explicit Writer(const Limits& l):Archive(l){}
    void word(std::uint64_t& x){tick();if(words.size()>=limits.bytes/8)throw Refusal("wire-byte-limit");words.push_back(x);}
    void count(std::uint64_t& n,std::size_t maximum){if(n>maximum)throw Refusal("wire-count-limit");word(n);}
};
struct Reader:Archive<Reader>
{
    static constexpr bool reading=true;const std::vector<std::uint64_t>& words;std::size_t pos=0;
    Reader(const std::vector<std::uint64_t>& w,const Limits& l):Archive(l),words(w)
    {if(w.size()>l.bytes/8)throw Refusal("wire-byte-limit");}
    void word(std::uint64_t& x){tick();if(pos>=words.size())throw Refusal("wire-truncated");x=words[pos++];}
    void count(std::uint64_t& n,std::size_t maximum){word(n);if(n>maximum||n>words.size()-pos)throw Refusal("wire-count-or-truncated");}
};
template<>struct Serde<std::uint64_t>{template<class A>static void io(A& a,std::uint64_t& x){a.word(x);}};
template<>struct Serde<std::int64_t>
{
    template<class A>static void io(A& a,std::int64_t& x)
    {std::uint64_t u=static_cast<std::uint64_t>(x);a.word(u);
        if(A::reading)x=u<=std::uint64_t(std::numeric_limits<std::int64_t>::max())?static_cast<std::int64_t>(u):-1-static_cast<std::int64_t>(~u);}
};
template<>struct Serde<int>
{
    template<class A>static void io(A& a,int& x){std::int64_t n=x;Serde<std::int64_t>::io(a,n);
        if(n<std::numeric_limits<int>::min()||n>std::numeric_limits<int>::max())throw Refusal("wire-int-range");
        if(A::reading)x=static_cast<int>(n);}
};
template<>struct Serde<bool>
{template<class A>static void io(A& a,bool& x){std::uint64_t n=x?1:0;a.word(n);if(n>1)throw Refusal("wire-bool");if(A::reading)x=n!=0;}};
template<>struct Serde<double>
{
    template<class A>static void io(A& a,double& x)
    {static_assert(sizeof(double)==8&&std::numeric_limits<double>::is_iec559,"binary64 required");
        std::uint64_t bits=0;std::memcpy(&bits,&x,8);a.word(bits);
        if((bits&UINT64_C(0x7ff0000000000000))==UINT64_C(0x7ff0000000000000))throw Refusal("wire-nonfinite");
        if(A::reading)std::memcpy(&x,&bits,8);}
};
template<>struct Serde<std::string>
{
    template<class A>static void io(A& a,std::string& s)
    {
        std::uint64_t n=s.size();a.word(n);if(n>a.limits.stringBytes)throw Refusal("wire-string-limit");
        if(A::reading){a.allocate(n,1);s.assign(static_cast<std::size_t>(n),'\0');}
        for(std::uint64_t i=0;i<n;i+=8)
        {
            std::uint64_t w=0;
            if(!A::reading)for(unsigned j=0;j<8&&i+j<n;++j)w|=std::uint64_t(static_cast<unsigned char>(s[std::size_t(i+j)]))<<(8*j);
            a.word(w);
            if(A::reading){const unsigned used=unsigned(std::min<std::uint64_t>(8,n-i));
                if(used<8&&(w>>(8*used))!=0)throw Refusal("wire-string-padding");
                for(unsigned j=0;j<used;++j)s[std::size_t(i+j)]=char((w>>(8*j))&255);}
        }
    }
};
template<class T,std::size_t N>struct Serde<std::array<T,N>>
{template<class A>static void io(A& a,std::array<T,N>& v){for(auto& x:v)a.field(x);}};
template<class T>struct Serde<std::vector<T>>
{
    template<class A>static void io(A& a,std::vector<T>& v)
    {std::uint64_t n=v.size();a.count(n,a.recordLimit());
        if(A::reading){a.allocate(n,sizeof(T));v.resize(std::size_t(n));}for(auto& x:v)a.field(x);}
};
template<class X,class Y>struct Serde<std::pair<X,Y>>
{template<class A>static void io(A& a,std::pair<X,Y>& p){a.fields(p.first,p.second);}};
template<class K,class V>struct Serde<std::map<K,V>>
{
    template<class A>static void io(A& a,std::map<K,V>& m)
    {
        std::uint64_t n=m.size();a.count(n,a.recordLimit());
        if(A::reading)
        {
            a.allocate(n,sizeof(std::pair<const K,V>)+4*sizeof(void*));m.clear();K last{};bool first=true;
            for(std::uint64_t i=0;i<n;++i){K k{};V v{};a.fields(k,v);
                if(!first&&!(last<k))throw Refusal("wire-map-order-or-duplicate");
                if(!m.emplace(k,std::move(v)).second)throw Refusal("wire-map-duplicate");last=k;first=false;}
        }
        else for(const auto& e:m){K k=e.first;V v=e.second;a.fields(k,v);}
    }
};
template<>struct Serde<D::Integer>
{
    template<class A>static void io(A& a,D::Integer& x)
    {
        std::uint64_t negative=x<0?1:0;std::vector<std::uint64_t> limbs;
        if(!A::reading){D::Integer magnitude=x;if(negative)magnitude=-magnitude;
            if(magnitude!=0)boost::multiprecision::export_bits(magnitude,std::back_inserter(limbs),64,false);}
        a.word(negative);if(negative>1)throw Refusal("wire-integer-sign");
        std::uint64_t n=limbs.size();a.count(n,256);
        if(A::reading){a.allocate(n,8);limbs.resize(std::size_t(n));}for(auto& limb:limbs)a.word(limb);
        if(n&&limbs.back()==0)throw Refusal("wire-integer-noncanonical");
        if(!n&&negative)throw Refusal("wire-integer-negative-zero");
        if(A::reading){x=0;if(n)boost::multiprecision::import_bits(x,limbs.begin(),limbs.end(),64,false);if(negative)x=-x;}
    }
};
template<>struct Serde<D::Rational>
{
    template<class A>static void io(A& a,D::Rational& x)
    {
        D::Integer n=x.numerator(),d=x.denominator();a.fields(n,d);if(d<=0)throw Refusal("wire-rational-denominator");
        if(A::reading){a.tick();D::Rational canonical(n,d);a.tick();
            if(canonical.numerator()!=n||canonical.denominator()!=d)throw Refusal("wire-rational-noncanonical");x=std::move(canonical);}
    }
};
template<>struct Serde<D::Role>
{template<class A>static void io(A& a,D::Role& x){int n=int(x);a.field(n);if(n<0||n>2)throw Refusal("wire-role");if(A::reading)x=D::Role(n);}};
template<>struct Serde<D::CoreMaterial>
{template<class A>static void io(A& a,D::CoreMaterial& x){int n=int(x);a.field(n);if(n<0||n>1)throw Refusal("wire-material");if(A::reading)x=D::CoreMaterial(n);}};
template<>struct Serde<D::Event::Kind>
{template<class A>static void io(A& a,D::Event::Kind& x){int n=int(x);a.field(n);if(n<0||n>8)throw Refusal("wire-event");if(A::reading)x=D::Event::Kind(n);}};

#define DCCART_WIRE_RECORD(Type,...) \
template<>struct Serde<Type>{template<class A>static void io(A& a,Type& x){a.fields(__VA_ARGS__);}};
DCCART_WIRE_RECORD(Identity,x.caseIdentity,x.inputSha,x.sourceCatalogueSha,x.surface,x.shell,x.geometryEpoch,x.membershipEpoch)
DCCART_WIRE_RECORD(Cell,x.id,x.ownerRank,x.level,x.leaf,x.piece,x.alive,x.origAlive,x.complete,x.refVolume,x.refCentre,x.faces)
DCCART_WIRE_RECORD(Face,x.id,x.owner,x.neighbour,x.patch,x.zone,x.alive,x.points)
DCCART_WIRE_RECORD(PointRecord,x.id,x.p,x.side,x.exactPin,x.footprint)
DCCART_WIRE_RECORD(FullStar,x.cells,x.faces,x.points,x.exactConstruction)
DCCART_WIRE_RECORD(D::NativeFacet,x.surface,x.shell,x.triangle,x.localRegion,x.globalRegion,x.pointIds,x.p,x.normal,x.geometryEpoch,x.membershipEpoch,x.sourceObjectUid,x.sourceFaceUid,x.representation)
DCCART_WIRE_RECORD(D::Window,x.surface,x.shell,x.geometryEpoch,x.membershipEpoch,x.requestedMin,x.requestedMax,x.complete,x.facets)
DCCART_WIRE_RECORD(D::OriginalFace,x.id,x.owner,x.neighbour,x.patch,x.zone,x.pointIds,x.points)
DCCART_WIRE_RECORD(D::OriginalExactPoint,x.exactPoint,x.storedPoint,x.surface,x.shell,x.geometryEpoch,x.membershipEpoch)
DCCART_WIRE_RECORD(D::OriginalCell,x.id,x.faces,x.pointSides,x.exactPoints)
DCCART_WIRE_RECORD(D::Limits,x.facets,x.points,x.facesPerPart,x.parts,x.ring,x.steps)
DCCART_WIRE_RECORD(D::Event,x.kind,x.surface,x.shell,x.sourceTriangle,x.originalFace,x.originalPoint,x.sourceEdge,x.originalEdge,x.geometryEpoch,x.membershipEpoch,x.constructionCell,x.sourceCellFace)
DCCART_WIRE_RECORD(D::Node,x.p,x.exactPoint,x.events)
DCCART_WIRE_RECORD(D::Face,x.ring,x.role,x.originalFace,x.sourceTriangle,x.region,x.zone,x.sourceEdge,x.sourceCellFace,x.sourceCellFaces)
DCCART_WIRE_RECORD(D::Part,x.faces,x.sourceTriangles,x.volumeCentroid,x.firstMoment,x.volume,x.kernelMargin)
DCCART_WIRE_RECORD(D::OriginalFacePlan,x.originalFace,x.owner,x.neighbour,x.patch,x.zone,x.fluidPartFaces,x.solidPartFaces,x.solidPartFacePieces)
DCCART_WIRE_RECORD(D::Contact,x.sourceTriangle,x.originalFace,x.region,x.ring,x.solidOnCurrentCellSide)
DCCART_WIRE_RECORD(D::OriginalPointContact,x.originalPoint,x.sourceTriangle,x.surface,x.shell,x.previousSide,x.geometryEpoch,x.membershipEpoch)
DCCART_WIRE_RECORD(D::Output,x.originalCell,x.nodes,x.fluidParts,x.solidPart,x.solidPartKnown,x.solidKernelMargin,x.solidParts,x.solidPartsKnown,x.coreMaterial,x.corePoint,x.coreMargin,x.originalFacePlans,x.contacts,x.originalPointContacts,x.positiveSourceFragments,x.interiorSourceFragments,x.internalCurtainPairs,x.solidKernel,x.geometricKnown)
template<>struct Serde<D::Input>
{
    template<class A>static void io(A& a,D::Input& x)
    {a.fields(x.cell,x.window,x.limits,x.mergeAdjacentSectors);
        if(A::reading){x.available={};x.solidMembership={};x.materialClassification={};x.leaseHeld={};}}
};
DCCART_WIRE_RECORD(Request,x.senderRank,x.ownerRank,x.level,x.leaf,x.originalCell,x.star,x.identity)
DCCART_WIRE_RECORD(OwnerPacket,x.ownerRank,x.transaction,x.seed,x.identity,x.star,x.input,x.output)
#undef DCCART_WIRE_RECORD
inline void require(bool v,const char* reason){if(!v)throw Refusal(reason);}
inline bool bitsEqual(const D::Point& a,const D::Point& b){return std::memcmp(a.data(),b.data(),3*sizeof(double))==0;}
inline void ring(const std::vector<Id>& r,const Limits& l)
{require(r.size()>=3&&r.size()<=l.ring,"wire-ring-size");std::set<Id> ids;
    for(auto p:r)require(p>=0&&ids.insert(p).second,"wire-ring-id");}
inline void ringNodes(const std::vector<std::size_t>& r,std::size_t n,const Limits& l)
{require(r.size()>=3&&r.size()<=l.ring,"wire-output-ring-size");std::set<std::size_t> ids;
    for(auto p:r)require(p<n&&ids.insert(p).second,"wire-output-ring-id");}
inline void validate(const FullStar& s,const Limits& l)
{
    require(!s.cells.empty()&&s.cells.size()<=l.cells&&s.faces.size()<=l.faces&&s.points.size()<=l.points,"wire-star-size");
    std::map<Id,const Cell*> cells;std::map<Id,const Face*> faces;std::map<Id,const PointRecord*> points;
    for(const auto& c:s.cells)
        require(c.id>=0&&c.ownerRank>=0&&c.level>=0&&c.level<=62&&(c.leaf>>63)==0
          &&cells.emplace(c.id,&c).second&&c.faces.size()<=l.faces,"wire-star-cell");
    for(const auto& p:s.points)
        require(p.id>=0&&p.side>=-1&&p.side<=1&&p.footprint.empty()&&points.emplace(p.id,&p).second,"wire-star-point");
    // Extract-local ancestry is intentionally not portable. The holder retains
    // its real footprints and performs its own complete-point-star checks.
    for(const auto& f:s.faces)
    {
        require(f.id>=0&&f.owner>=0&&f.neighbour>=-1&&f.neighbour!=f.owner&&f.patch>=-1&&f.zone>=-1
          &&cells.count(f.owner)&&(f.neighbour<0||cells.count(f.neighbour))
          &&faces.emplace(f.id,&f).second,"wire-star-face");
        ring(f.points,l);for(auto p:f.points)require(points.count(p),"wire-star-point-missing");
    }
    for(const auto& c:s.cells)
    {
        std::set<Id> ids;
        for(auto f:c.faces){require(ids.insert(f).second&&faces.count(f),"wire-star-cell-face");
            require(faces.at(f)->owner==c.id||faces.at(f)->neighbour==c.id,"wire-star-incidence");}
    }
    for(const auto& f:s.faces)for(Id c:{f.owner,f.neighbour})if(c>=0)
        require(std::count(cells.at(c)->faces.begin(),cells.at(c)->faces.end(),f.id)==1,"wire-star-reciprocal-incidence");
    for(const auto& e:s.exactConstruction)
        require(points.count(e.first)&&bitsEqual(points.at(e.first)->p,e.second.storedPoint)
          &&e.second.surface>=0&&e.second.shell>=0,"wire-star-exact-cache");
}
inline void validate(const D::OriginalCell& c,const FullStar& s,const Limits& l)
{
    require(c.id>=0&&!c.faces.empty()&&c.faces.size()<=l.faces,"wire-original-cell");
    std::map<Id,const Cell*> cells;std::map<Id,const Face*> faces;std::map<Id,const PointRecord*> points;
    for(const auto& v:s.cells)cells.emplace(v.id,&v);for(const auto& v:s.faces)faces.emplace(v.id,&v);
    for(const auto& v:s.points)points.emplace(v.id,&v);
    require(cells.count(c.id)&&cells.at(c.id)->complete,"wire-original-cell-incomplete");
    std::set<Id> actual,used;
    for(const auto& f:c.faces)
    {
        require(f.id>=0&&faces.count(f.id)&&actual.insert(f.id).second&&f.pointIds.size()==f.points.size(),"wire-original-face");
        const auto& b=*faces.at(f.id);
        require(f.owner==b.owner&&f.neighbour==b.neighbour&&f.patch==b.patch&&f.zone==b.zone
          &&f.pointIds==b.points&&b.alive,"wire-original-preimage");ring(f.pointIds,l);
        for(std::size_t i=0;i<f.pointIds.size();++i){auto p=f.pointIds[i];
            require(points.count(p)&&bitsEqual(f.points[i],points.at(p)->p),"wire-original-point-bits");used.insert(p);}
    }
    const std::set<Id> listed(cells.at(c.id)->faces.begin(),cells.at(c.id)->faces.end());
    require(actual==listed,"wire-original-face-coverage");
    for(const auto& e:c.pointSides)
        require(used.count(e.first)&&e.second>=-1&&e.second<=1&&e.second==points.at(e.first)->side,"wire-original-side");
    require(c.pointSides.size()==used.size(),"wire-original-side-missing");
    for(const auto& e:c.exactPoints)
        require(used.count(e.first)&&s.exactConstruction.count(e.first)&&e.second==s.exactConstruction.at(e.first),"wire-original-exact-cache");
}
inline void validate(const D::Window& w,const Identity& i,const Limits& l)
{
    require(w.surface>=0&&w.shell>=0&&w.surface==i.surface&&w.shell==i.shell
      &&w.geometryEpoch==i.geometryEpoch&&w.membershipEpoch==i.membershipEpoch
      &&w.complete&&w.facets.size()<=l.facets,"wire-window-identity");std::set<Id> ids;
    for(const auto& f:w.facets)
    {
        require(f.surface==w.surface&&f.shell==w.shell&&f.triangle>=0&&ids.insert(f.triangle).second
          &&f.geometryEpoch==w.geometryEpoch&&f.membershipEpoch==w.membershipEpoch
          &&f.globalRegion>=0&&f.localRegion>=0,"wire-native-facet");
        for(auto p:f.pointIds)require(p>=0,"wire-native-point-id");
    }
}
inline void validate(const Request& r,const Limits& l)
{
    require(r.senderRank>=0&&r.ownerRank>=0&&r.level>=0&&r.level<=62,"wire-request-identity");
    validate(r.star,l);validate(r.originalCell,r.star,l);const Cell* seed=nullptr;
    for(const auto& c:r.star.cells)if(c.id==r.originalCell.id)seed=&c;
    require(seed&&seed->ownerRank==r.ownerRank&&seed->level==r.level&&seed->leaf==r.leaf,"wire-request-key");
}
inline void validate(const OwnerPacket& p,const Limits& l)
{
    require(p.ownerRank>=0&&p.transaction>0&&p.seed>=0&&p.seed==p.input.cell.id&&p.seed==p.output.originalCell,"wire-owner-identity");
    validate(p.star,l);validate(p.input.cell,p.star,l);validate(p.input.window,p.identity,l);
    const Cell* seed=nullptr;for(const auto& c:p.star.cells)if(c.id==p.seed)seed=&c;
    require(seed&&seed->ownerRank==p.ownerRank,"wire-owner-seed");
    const auto& o=p.output;
    require(o.nodes.size()<=l.nodes&&o.fluidParts.size()<=l.parts&&o.solidParts.size()<=l.parts
      &&o.originalFacePlans.size()<=l.faces&&o.contacts.size()<=l.faces&&o.originalPointContacts.size()<=l.points,"wire-output-size");
    for(const auto& n:o.nodes)require(n.events.size()<=l.events,"wire-events-limit");
    auto part=[&](const D::Part& part){require(part.faces.size()<=l.faces,"wire-part-size");
        for(const auto& f:part.faces){ringNodes(f.ring,o.nodes.size(),l);require(f.sourceCellFaces.size()<=l.faces,"wire-source-cell-faces");}};
    for(const auto& x:o.fluidParts)part(x);for(const auto& x:o.solidParts)part(x);
    if(o.solidPartKnown)part(o.solidPart);else require(o.solidPart.faces.empty()&&o.solidPart.sourceTriangles.empty(),"wire-unused-solid-part");
    for(const auto& c:o.contacts)ringNodes(c.ring,o.nodes.size(),l);
    std::set<Id> plans,certs;
    for(const auto& f:o.originalFacePlans)
    {
        require(plans.insert(f.originalFace).second,"wire-duplicate-face-plan");
        for(const auto& r:f.fluidPartFaces)require(r.first<o.fluidParts.size()&&r.second<o.fluidParts[r.first].faces.size(),"wire-fluid-plan-ref");
        for(auto r:f.solidPartFaces)require(o.solidPartKnown&&r<o.solidPart.faces.size(),"wire-solid-plan-ref");
        for(const auto& r:f.solidPartFacePieces)
            require(o.solidPartsKnown&&r.first<o.solidParts.size()&&r.second<o.solidParts[r.first].faces.size(),"wire-solids-plan-ref");
    }
    for(const auto& c:o.originalPointContacts)
        require(certs.insert(c.originalPoint).second&&(c.previousSide==-1||c.previousSide==1),"wire-point-contact");
}
static constexpr std::uint64_t magic=UINT64_C(0x4443504f57495245);
template<class T>inline bool encodeImpl(const T& value,std::uint64_t kind,
    std::vector<std::uint64_t>& result,std::string* reason,const Limits& limits)
{
    try
    {
        validate(value,limits);Writer a(limits);std::uint64_t m=magic,v=1,n=0;a.fields(m,v,kind,n);
        T copy=value;a.field(copy);a.words[3]=a.words.size();a.tick();result.swap(a.words);
        if(reason)reason->clear();return true;
    }
    catch(const std::exception& e){if(reason)*reason=e.what();return false;}
    catch(...){if(reason)*reason="wire-exception";return false;}
}
template<class T>inline bool decodeImpl(const std::vector<std::uint64_t>& value,std::uint64_t kind,
    T& result,std::string* reason,const Limits& limits)
{
    try
    {
        Reader a(value,limits);std::uint64_t m=0,v=0,k=0,n=0;a.fields(m,v,k,n);
        require(m==magic&&v==1&&k==kind&&n==value.size(),"wire-header");
        T copy;a.field(copy);require(a.pos==value.size(),"wire-trailing-words");
        validate(copy,limits);a.tick();result=std::move(copy);if(reason)reason->clear();return true;
    }
    catch(const std::exception& e){if(reason)*reason=e.what();return false;}
    catch(...){if(reason)*reason="wire-exception";return false;}
}
inline Id mapped(Id id,const std::map<Id,Id>& m,const char* why,bool optional=true)
{if(id==-1&&optional)return -1;require(id>=0,why);auto p=m.find(id);require(p!=m.end()&&p->second>=0,why);return p->second;}
inline void bijective(const std::map<Id,Id>& m)
{std::set<Id> target;for(const auto& e:m)require(e.first>=0&&e.second>=0&&target.insert(e.second).second,"wire-remap-not-bijective");}
} // namespace detail
inline bool encode(const Request& p,std::vector<std::uint64_t>& w,std::string* r,const Limits& l){return detail::encodeImpl(p,1,w,r,l);}
inline bool encode(const OwnerPacket& p,std::vector<std::uint64_t>& w,std::string* r,const Limits& l){return detail::encodeImpl(p,2,w,r,l);}
inline bool decode(const std::vector<std::uint64_t>& w,Request& p,std::string* r,const Limits& l){return detail::decodeImpl(w,1,p,r,l);}
inline bool decode(const std::vector<std::uint64_t>& w,OwnerPacket& p,std::string* r,const Limits& l){return detail::decodeImpl(w,2,p,r,l);}

inline bool remapOwner(const OwnerPacket& sender,const Maps& map,OwnerPacket& result,
    std::string* reason,const Limits& limits)
{
    // No matching algorithm and no permissions here. The unique full-preimage
    // matcher supplies Maps; root glue uses its fresh holder-native Context.
    // result.star is intentionally EMPTY, not a renamed local ancestry proof.
    try
    {
        detail::validate(sender,limits);
        detail::bijective(map.cells);detail::bijective(map.faces);detail::bijective(map.points);
        detail::require(map.faceReversed.size()==map.faces.size(),"wire-remap-orientation-missing");
        for(const auto& e:map.faceReversed)detail::require(map.faces.count(e.first),"wire-remap-extra-orientation");
        OwnerPacket target=sender;std::size_t work=0;
        auto tick=[&](){if(++work>limits.steps)throw detail::Refusal("wire-remap-work-limit");
            if(limits.available&&!limits.available())throw detail::Refusal("wire-remap-clock");};
        auto cell=[&](Id x,bool optional=true){tick();return detail::mapped(x,map.cells,"wire-remap-cell",optional);};
        auto point=[&](Id x,bool optional=true){tick();return detail::mapped(x,map.points,"wire-remap-point",optional);};
        auto face=[&](Id x,bool optional=true){tick();return detail::mapped(x,map.faces,"wire-remap-face",optional);};
        auto reversed=[&](Id x){tick();auto i=map.faceReversed.find(x);
            detail::require(i!=map.faceReversed.end(),"wire-remap-face-orientation");return i->second;};
        target.seed=cell(sender.seed,false);target.input.cell.id=cell(sender.input.cell.id,false);
        for(auto& f:target.input.cell.faces)
        {
            const Id old=f.id;const bool flip=reversed(old);f.id=face(old,false);
            f.owner=cell(f.owner,false);f.neighbour=cell(f.neighbour);
            for(auto& p:f.pointIds)p=point(p,false);
            if(flip)
            {
                detail::require(f.neighbour>=0,"wire-remap-boundary-reversed");
                std::reverse(f.pointIds.begin(),f.pointIds.end());std::reverse(f.points.begin(),f.points.end());
                std::swap(f.owner,f.neighbour);
            }
        }
        std::map<Id,int> sides;
        for(const auto& e:sender.input.cell.pointSides)
            detail::require(sides.emplace(point(e.first,false),e.second).second,"wire-remap-side-collision");
        target.input.cell.pointSides.swap(sides);
        std::map<Id,D::OriginalExactPoint> exact;
        for(const auto& e:sender.input.cell.exactPoints)
            detail::require(exact.emplace(point(e.first,false),e.second).second,"wire-remap-cache-collision");
        target.input.cell.exactPoints.swap(exact);
        target.input.available={};target.input.solidMembership={};target.input.materialClassification={};target.input.leaseHeld={};
        target.output.originalCell=cell(sender.output.originalCell,false);
        for(auto& n:target.output.nodes)for(auto& e:n.events)
        {
            tick();e.originalFace=face(e.originalFace);
            // sourcePoint.originalPoint belongs to the prepared native source
            // point namespace; only other events carry an optional mesh point.
            if(e.kind==D::Event::Kind::sourcePoint)tick();
            else e.originalPoint=point(e.originalPoint);
            for(auto& p:e.originalEdge)p=point(p);
            if(e.originalEdge[0]>=0&&e.originalEdge[1]>=0&&e.originalEdge[1]<e.originalEdge[0])std::swap(e.originalEdge[0],e.originalEdge[1]);
            e.constructionCell=cell(e.constructionCell);e.sourceCellFace=face(e.sourceCellFace);
            // Actual native sourceTriangle/sourceEdge IDs remain unchanged.
        }
        auto part=[&](D::Part& p)
        {
            for(auto& f:p.faces){tick();f.originalFace=face(f.originalFace);f.sourceCellFace=face(f.sourceCellFace);
                for(auto& id:f.sourceCellFaces)id=face(id,false);}
            // Output cycles are seed-outward; wire-local node indices do NOT
            // reverse merely because the holder's stored original face reverses.
        };
        for(auto& p:target.output.fluidParts)part(p);for(auto& p:target.output.solidParts)part(p);
        if(target.output.solidPartKnown)part(target.output.solidPart);
        for(auto& p:target.output.originalFacePlans)
        {
            const Id old=p.originalFace;const bool flip=reversed(old);
            p.originalFace=face(old,false);p.owner=cell(p.owner,false);p.neighbour=cell(p.neighbour);
            if(flip){detail::require(p.neighbour>=0,"wire-remap-boundary-plan-reversed");std::swap(p.owner,p.neighbour);}
        }
        for(auto& c:target.output.contacts)c.originalFace=face(c.originalFace,false);
        for(auto& c:target.output.originalPointContacts)c.originalPoint=point(c.originalPoint,false);
        for(const auto& e:sender.star.exactConstruction)if(map.points.count(e.first))
        {
            tick();const auto id=point(e.first,false);const auto found=target.input.cell.exactPoints.find(id);
            if(found!=target.input.cell.exactPoints.end())detail::require(found->second==e.second,"wire-remap-cache-conflict");
        }
        target.star=FullStar();tick();result=std::move(target);if(reason)reason->clear();return true;
    }
    catch(const std::exception& e){if(reason)*reason=e.what();return false;}
    catch(...){if(reason)*reason="wire-remap-exception";return false;}
}
} // namespace dcCartPreparedOwnerWire
#endif
