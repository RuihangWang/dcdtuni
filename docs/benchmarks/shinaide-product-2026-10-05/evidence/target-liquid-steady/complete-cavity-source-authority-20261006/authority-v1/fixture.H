#ifndef completeCavityFixture_H
#define completeCavityFixture_H
#include "dcCartCompleteCavityOriginCore.H"
#include <stdexcept>
using namespace dcCartCompleteCavityOriginCore;
using Snapshot=dcCartFluidOriginSnapshotCore::Snapshot;
using Identity=dcCartFluidOriginSnapshotCore::Identity;
inline Fingerprint proof()
{ return {std::string(64,'a'),std::string(64,'b'),"global-air:fixture",
          std::string(64,'c'),std::string(64,'d'),std::string(64,'e')}; }
inline Snapshot boxes(int count=5)
{
    Snapshot s;const auto p=proof();
    s.complete=s.completedFluidOnlyPath=true;s.rawSha256=p.raw;s.rolesDigest=p.roles;
    s.materialKey=p.materialKey;s.materialDigest=p.material;s.producerDigest=p.producer;
    s.boundaryMembershipDigest=p.boundaryMembership;
    std::map<std::tuple<int,int,int>,int> pointIds;
    auto point=[&](int x,int y,int z)
    {
        const auto key=std::make_tuple(x,y,z);const auto it=pointIds.find(key);
        if(it!=pointIds.end())return it->second;
        const int id=s.points.size();pointIds[key]=id;s.points.push_back({{double(x),double(y),double(z)}});return id;
    };
    s.parents.resize(count);s.valid.assign(count,1);int previousRight=-1;
    auto face=[&](int owner,std::vector<int> ring)
    {
        const int id=s.faces.size();
        s.faces.push_back({id,owner,-1,0,-1,ring});return id;
    };
    for(int c=0;c<count;++c)
    {
        s.parents[c].identity=Identity(0,c,2,100+c,200+c);
        int a=point(c,0,0),b=point(c,0,1),d=point(c,1,1),e=point(c,1,0);
        int f=point(c+1,0,0),g=point(c+1,0,1),h=point(c+1,1,1),i=point(c+1,1,0);
        const int left=c==0?face(c,{a,b,d,e}):previousRight;
        if(c>0) { s.faces[left].neighbour=c;s.faces[left].patch=-1; }
        const int right=face(c,{f,i,h,g});
        s.parents[c].facets={left,right,face(c,{a,f,g,b}),face(c,{e,d,h,i}),
                            face(c,{a,e,i,f}),face(c,{b,g,h,d})};
        previousRight=right;
    }
    return s;
}
inline std::vector<Origin> records(const Snapshot& s)
{
    std::vector<Origin> out;
    for(const auto& p:s.parents)
    {
        Origin o;o.identity=p.identity;
        for(int f:p.facets) {const auto& row=s.faces[f];o.facets.push_back({{row.handle,row.patch,row.zone}});}
        out.push_back(o);
    }
    return out;
}
inline void require(bool yes,const char* message) { if(!yes)throw std::runtime_error(message); }
#endif
