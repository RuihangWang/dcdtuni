#ifndef dcCartPreparedStarMatch_H
#define dcCartPreparedStarMatch_H
#include "dcCartPreparedOwnerWire.H"
#include <array>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <set>
#include <tuple>
#include <stdexcept>

// Transaction-local preimage identity only. No source/material/ownership
// permission is granted. Both callers must independently capture a fresh,
// complete affected star before Install and the collective commit vote.
namespace dcCartPreparedStarMatch
{
namespace W=::dcCartPreparedOwnerWire;
namespace D=::dcCartPreparedFacetPartition;
using Id=W::Id;
namespace detail
{
struct Refusal:std::runtime_error { using std::runtime_error::runtime_error; };
struct Budget
{
    const std::function<bool()>& available;
    std::size_t steps=0;
    void tick(std::size_t n=1)
    {
        if(!available)throw Refusal("match-missing-available");
        if(n>4000000-steps)throw Refusal("match-step-cap");
        steps+=n;
        if(!available())throw Refusal("match-unavailable");
    }
};
inline void setRefusal(std::string* out,const char* text)
{
    if(out){*out=text?text:"match-empty-exception";if(out->size()>256)out->resize(256);}
}
using Bits=std::array<std::uint64_t,3>;
inline Bits bits(const D::Point& p)
{
    Bits b;
    for(std::size_t j=0;j<3;++j)
    {
        if(!std::isfinite(p[j]))throw Refusal("match-nonfinite-point");
        std::memcpy(&b[j],&p[j],sizeof(double));
    }
    return b;
}
inline std::uint64_t bits(double x)
{
    if(!std::isfinite(x))throw Refusal("match-nonfinite-reference");
    std::uint64_t b;std::memcpy(&b,&x,sizeof(double));return b;
}
template<class T> inline std::vector<T> cycle(std::vector<T> q)
{
    // IDs/bits in a valid ring are unique, hence its minimum rotation is unique.
    if(q.empty())throw Refusal("match-empty-ring");
    const auto it=std::min_element(q.begin(),q.end());
    std::rotate(q.begin(),it,q.end());return q;
}
using Family=std::tuple<int,int,std::uint64_t>;
inline Family family(const W::Cell& c){return {c.ownerRank,c.level,c.leaf};}
struct Index
{
    std::map<Id,const W::Cell*> cells;
    std::map<Id,const W::Face*> faces;
    std::map<Id,const W::PointRecord*> points;
    std::set<Id> used;
    std::map<Bits,Id> coordinate;
};
inline Index index(const W::FullStar& s,Budget& b)
{
    const W::Limits l;
    if(s.cells.empty()||s.cells.size()>l.cells||s.faces.empty()
      ||s.faces.size()>l.faces||s.points.size()>l.points)
        throw Refusal("match-table-cap");
    Index x;
    for(const auto& c:s.cells)
    {
        b.tick();
        if(c.id<0||c.ownerRank<0||c.level<0||c.faces.size()>l.faces
          ||!x.cells.emplace(c.id,&c).second)
            throw Refusal("match-invalid-cell");
        bits(c.refVolume);bits(c.refCentre);
    }
    for(const auto& p:s.points)
    {
        b.tick();
        if(p.id<0||p.side<-1||p.side>1||p.footprint.size()>l.points
          ||!x.points.emplace(p.id,&p).second)
            throw Refusal("match-invalid-point");
        bits(p.p);
    }
    std::map<Id,std::set<Id>> incidence;
    for(const auto& f:s.faces)
    {
        b.tick();
        if(f.id<0||!f.alive||f.owner<0||f.neighbour<-1
          ||f.neighbour==f.owner||f.patch<-1||f.zone<-1
          ||f.points.size()<3||f.points.size()>l.ring
          ||!x.faces.emplace(f.id,&f).second||!x.cells.count(f.owner)
          ||(f.neighbour>=0&&!x.cells.count(f.neighbour)))
            throw Refusal("match-invalid-face");
        std::set<Id> q;
        for(const auto p:f.points)
        {
            b.tick();
            if(!x.points.count(p)||!q.insert(p).second)
                throw Refusal("match-invalid-ring");
            x.used.insert(p);
        }
        incidence[f.owner].insert(f.id);
        if(f.neighbour>=0)incidence[f.neighbour].insert(f.id);
    }
    for(const auto& c:s.cells)
    {
        b.tick(c.faces.size()+1);
        std::set<Id> q(c.faces.begin(),c.faces.end());
        if(q.size()!=c.faces.size()||q!=incidence[c.id])
            throw Refusal("match-cell-face-incidence");
        if(c.complete && (!c.alive||c.faces.empty()))
            throw Refusal("match-incomplete-live-cell");
    }
    for(const auto p:x.used)
    {
        b.tick();
        if(!x.coordinate.emplace(bits(x.points.at(p)->p),p).second)
            throw Refusal("match-ambiguous-coordinate");
    }
    // Auxiliary footprint roots may be absent from active faces. They are
    // local completeness evidence, not cross-rank identity or Maps entries.
    for(const auto& p:s.points)
        for(const auto root:p.footprint)
        {
            b.tick();
            if(!x.points.count(root))throw Refusal("match-missing-footprint-root");
        }
    for(const auto& e:s.exactConstruction)
    {
        b.tick();
        const auto p=x.points.find(e.first);
        if(p==x.points.end()||bits(e.second.storedPoint)!=bits(p->second->p))
            throw Refusal("match-exact-stored-bytes");
    }
    return x;
}
struct CellFaceKey
{
    Id patch=-1,zone=-1;bool alive=false,boundary=false;
    Family other;
    std::vector<Id> outward;
    bool operator<(const CellFaceKey& b)const
    {
        return std::tie(patch,zone,alive,boundary,other,outward)
          <std::tie(b.patch,b.zone,b.alive,b.boundary,b.other,b.outward);
    }
};
struct CellKey
{
    Family raw;bool alive=false,origAlive=false,complete=false;
    std::uint64_t refVolume=0;Bits refCentre;
    std::vector<CellFaceKey> faces;
    bool operator<(const CellKey& b)const
    {
        return std::tie(raw,alive,origAlive,complete,refVolume,refCentre,faces)
          <std::tie(b.raw,b.alive,b.origAlive,b.complete,b.refVolume,b.refCentre,b.faces);
    }
};
inline CellKey cellKey(const W::Cell& c,const Index& x,
                      const std::map<Id,Id>* pm,Budget& b)
{
    CellKey k;k.raw=family(c);k.alive=c.alive;k.origAlive=c.origAlive;
    k.complete=c.complete;k.refVolume=bits(c.refVolume);k.refCentre=bits(c.refCentre);
    for(const auto id:c.faces)
    {
        b.tick();
        const auto& f=*x.faces.at(id);
        CellFaceKey v;v.patch=f.patch;v.zone=f.zone;v.alive=f.alive;
        const Id other=f.owner==c.id?f.neighbour:f.owner;
        v.boundary=other<0;
        v.other=other<0?Family{-1,-1,0}:family(*x.cells.at(other));
        for(const auto p:f.points){b.tick();v.outward.push_back(pm?pm->at(p):p);}
        if(f.neighbour==c.id)std::reverse(v.outward.begin(),v.outward.end());
        v.outward=cycle(std::move(v.outward));
        k.faces.push_back(std::move(v));
    }
    std::sort(k.faces.begin(),k.faces.end());return k;
}
struct FaceKey
{
    Id lo=-1,hi=-1,patch=-1,zone=-1;bool alive=false;
    std::vector<Id> outwardLo;
    bool operator<(const FaceKey& b)const
    {
        return std::tie(lo,hi,patch,zone,alive,outwardLo)
          <std::tie(b.lo,b.hi,b.patch,b.zone,b.alive,b.outwardLo);
    }
};
inline FaceKey faceKey(const W::Face& f,const W::Maps* m,Budget& b)
{
    FaceKey k;
    const Id owner=m?m->cells.at(f.owner):f.owner;
    const Id neighbour=f.neighbour<0?-1:(m?m->cells.at(f.neighbour):f.neighbour);
    k.lo=neighbour<0?owner:std::min(owner,neighbour);
    k.hi=neighbour<0?-1:std::max(owner,neighbour);
    k.patch=f.patch;k.zone=f.zone;k.alive=f.alive;
    for(const auto p:f.points){b.tick();k.outwardLo.push_back(m?m->points.at(p):p);}
    if(owner!=k.lo)std::reverse(k.outwardLo.begin(),k.outwardLo.end());
    k.outwardLo=cycle(std::move(k.outwardLo));return k;
}
using GeometricFace=std::tuple<Id,Id,std::vector<Bits>>;
inline std::vector<GeometricFace> originalShape(const D::OriginalCell& c,Budget& b)
{
    if(c.id<0||c.faces.empty()||c.faces.size()>W::Limits().faces)
        throw Refusal("locate-invalid-original");
    std::vector<GeometricFace> k;
    std::set<Id> faceIds;
    for(const auto& f:c.faces)
    {
        b.tick();
        if(f.id<0||!faceIds.insert(f.id).second||f.points.size()<3
          ||f.points.size()>W::Limits().ring||f.points.size()!=f.pointIds.size()
          ||(f.owner!=c.id&&f.neighbour!=c.id)||f.patch<-1||f.zone<-1)
            throw Refusal("locate-invalid-original-face");
        std::vector<Bits> q;std::set<Bits> unique;
        for(const auto& p:f.points)
        {
            b.tick();const auto v=bits(p);q.push_back(v);
            if(!unique.insert(v).second)throw Refusal("locate-ambiguous-coordinate");
        }
        if(f.neighbour==c.id)std::reverse(q.begin(),q.end());
        k.emplace_back(f.patch,f.zone,cycle(std::move(q)));
    }
    std::sort(k.begin(),k.end());return k;
}
inline std::vector<GeometricFace> localShape(const W::Cell& c,const Index& x,Budget& b)
{
    std::vector<GeometricFace> k;
    for(const auto id:c.faces)
    {
        b.tick();const auto& f=*x.faces.at(id);std::vector<Bits> q;
        for(const auto p:f.points){b.tick();q.push_back(bits(x.points.at(p)->p));}
        if(f.neighbour==c.id)std::reverse(q.begin(),q.end());
        k.emplace_back(f.patch,f.zone,cycle(std::move(q)));
    }
    std::sort(k.begin(),k.end());return k;
}
} // namespace detail

inline bool match(const W::FullStar& sender,const W::FullStar& receiver,
                  const std::function<bool()>& available,W::Maps& out,
                  std::string* refusal=nullptr)
{
    out=W::Maps();if(refusal)refusal->clear();
    try
    {
        detail::Budget b{available};b.tick();
        const auto a=detail::index(sender,b),z=detail::index(receiver,b);
        if(a.cells.size()!=z.cells.size()||a.faces.size()!=z.faces.size()
          ||a.used.size()!=z.used.size())
            throw detail::Refusal("match-preimage-size sender(c/f/usedP)="
              +std::to_string(a.cells.size())+"/"+std::to_string(a.faces.size())+"/"
              +std::to_string(a.used.size())+" receiver="
              +std::to_string(z.cells.size())+"/"+std::to_string(z.faces.size())+"/"
              +std::to_string(z.used.size()));
        W::Maps m;
        for(const auto p:a.used)
        {
            b.tick();const auto& pa=*a.points.at(p);
            const auto q=z.coordinate.find(detail::bits(pa.p));
            if(q==z.coordinate.end())throw detail::Refusal("match-point-bytes");
            const auto& pb=*z.points.at(q->second);
            if(pa.side!=pb.side||pa.exactPin!=pb.exactPin)
                throw detail::Refusal("match-point-state senderId="+std::to_string(p)
                  +" receiverId="+std::to_string(q->second)
                  +" side="+std::to_string(pa.side)+"/"+std::to_string(pb.side)
                  +" pin="+std::to_string(pa.exactPin)+"/"+std::to_string(pb.exactPin));
            const auto ea=sender.exactConstruction.find(p);
            const auto eb=receiver.exactConstruction.find(q->second);
            if((ea==sender.exactConstruction.end())!=(eb==receiver.exactConstruction.end())
              ||(ea!=sender.exactConstruction.end()&&ea->second!=eb->second))
                throw detail::Refusal("match-exact-construction");
            m.points.emplace(p,q->second);
        }
        std::map<detail::CellKey,Id> candidate;
        for(const auto& c:receiver.cells)
        {
            b.tick();
            if(!candidate.emplace(detail::cellKey(c,z,nullptr,b),c.id).second)
                throw detail::Refusal("match-ambiguous-cell");
        }
        std::set<Id> usedCells;
        for(const auto& c:sender.cells)
        {
            b.tick();const auto q=candidate.find(detail::cellKey(c,a,&m.points,b));
            if(q==candidate.end())
                throw detail::Refusal("match-cell-topology senderId="+std::to_string(c.id)
                  +" rawOwner="+std::to_string(c.ownerRank)
                  +" level="+std::to_string(c.level)+" leaf="+std::to_string(c.leaf));
            if(!usedCells.insert(q->second).second)throw detail::Refusal("match-cell-bijection");
            m.cells.emplace(c.id,q->second);
        }
        std::map<detail::FaceKey,Id> faceCandidates;
        for(const auto& f:receiver.faces)
        {
            b.tick();
            if(!faceCandidates.emplace(detail::faceKey(f,nullptr,b),f.id).second)
                throw detail::Refusal("match-ambiguous-face");
        }
        std::set<Id> usedFaces;
        for(const auto& f:sender.faces)
        {
            b.tick();const auto q=faceCandidates.find(detail::faceKey(f,&m,b));
            if(q==faceCandidates.end())throw detail::Refusal("match-face-topology");
            if(!usedFaces.insert(q->second).second)throw detail::Refusal("match-face-bijection");
            const auto& rf=*z.faces.at(q->second);
            const bool reversed=m.cells.at(f.owner)!=rf.owner;
            if(reversed && (f.neighbour<0||rf.neighbour<0
              ||m.cells.at(f.neighbour)!=rf.owner||m.cells.at(f.owner)!=rf.neighbour))
                throw detail::Refusal("match-face-orientation");
            m.faces.emplace(f.id,q->second);m.faceReversed.emplace(f.id,reversed);
        }
        b.tick();out=std::move(m);return true;
    }
    catch(const std::exception& e){detail::setRefusal(refusal,e.what());}
    catch(...){detail::setRefusal(refusal,"match-unknown-exception");}
    return false;
}

// Locator is a preimage finder, not authority. The receiver FullStar must have
// fresh, complete cell records. A raw owner/level/leaf match alone is insufficient.
// piece never participates. A second geometric candidate is an honest refusal.
inline bool locate(const D::OriginalCell& original,int ownerRank,int level,
                   std::uint64_t leaf,const W::FullStar& receiver,
                   const std::function<bool()>& available,Id& seed,
                   std::string* refusal=nullptr)
{
    seed=-1;if(refusal)refusal->clear();
    try
    {
        detail::Budget b{available};b.tick();
        if(ownerRank<0||level<0)throw detail::Refusal("locate-invalid-family");
        const auto x=detail::index(receiver,b);
        const auto target=detail::originalShape(original,b);
        Id found=-1;
        for(const auto& c:receiver.cells)
        {
            b.tick();
            if(!c.alive||!c.complete||c.ownerRank!=ownerRank||c.level!=level||c.leaf!=leaf)continue;
            if(detail::localShape(c,x,b)!=target)continue;
            if(found>=0)throw detail::Refusal("locate-ambiguous-cell");
            found=c.id;
        }
        if(found<0)throw detail::Refusal("locate-no-preimage");
        b.tick();seed=found;return true;
    }
    catch(const std::exception& e){detail::setRefusal(refusal,e.what());}
    catch(...){detail::setRefusal(refusal,"locate-unknown-exception");}
    return false;
}
} // namespace dcCartPreparedStarMatch
#endif
