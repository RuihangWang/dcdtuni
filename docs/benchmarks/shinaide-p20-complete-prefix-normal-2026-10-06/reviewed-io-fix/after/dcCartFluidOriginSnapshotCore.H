#ifndef dcCartFluidOriginSnapshotCore_H
#define dcCartFluidOriginSnapshotCore_H
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <tuple>
#include <vector>
namespace dcCartFluidOriginSnapshotCore {
using Coord=std::array<double,3>;
using Identity=std::tuple<int,int,int,std::uint64_t,std::uint64_t>; // rank,parent,level,leaf,piece
struct Facet {
    int handle=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    std::vector<int> ring;
};
struct Parent {
    Identity identity;
    std::vector<int> facets;
};
struct Snapshot {
    bool completedFluidOnlyPath=false,complete=false;
    std::string rawSha256,rolesDigest,materialKey,materialDigest,producerDigest;
    // Fresh normal boundary-role membership, independent of face/IO offsets.
    std::string boundaryMembershipDigest;
    std::vector<Coord> points;
    std::vector<Facet> faces;
    std::vector<Parent> parents;
    std::vector<unsigned char> valid;
};
inline Coord cross(const Coord& a,const Coord& b) {
    return {{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}};
}
inline double dot(const Coord& a,const Coord& b) {
    return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
}
// Structural ownership screen only; actual installed tet/geometry/quality
// and unchanged-coupled certificates are independent mandatory guards.
inline bool validShape(const Snapshot& s,const Parent& p) {
    const int parent=std::get<1>(p.identity);
    if (parent<0 || std::get<0>(p.identity)<0 || std::get<2>(p.identity)<0
     || p.facets.size()<4 || p.facets.size()>512) return false;
    std::map<std::pair<int,int>,int> edges;
    std::set<int> uniqueFaces;
    double volume6=0;
    for (int f:p.facets) {
        if (f<0 || f>=int(s.faces.size()) || !uniqueFaces.insert(f).second) return false;
        const Facet& face=s.faces[f];
        if (face.handle!=f || face.ring.size()<3 || face.ring.size()>512
         || face.patch<-1 || face.zone<-1
         || (face.owner!=parent && face.neighbour!=parent)) return false;
        std::vector<int> ring(face.ring);
        if (face.neighbour==parent) std::reverse(ring.begin()+1,ring.end());
        std::set<int> ids;
        for (int id:ring) {
            if (id<0 || id>=int(s.points.size()) || !ids.insert(id).second) return false;
            for(double x:s.points[id]) if(!std::isfinite(x)) return false;
        }
        for(std::size_t i=0;i<ring.size();++i) ++edges[{ring[i],ring[(i+1)%ring.size()]}];
        const Coord& a=s.points[ring[0]];
        for (std::size_t i=1;i+1<ring.size();++i)
            volume6+=dot(a,cross(s.points[ring[i]],s.points[ring[i+1]]));
    }
    if (!std::isfinite(volume6) || volume6<=0) return false;
    for (const auto& row:edges)
        if(row.second!=1 || edges[{row.first.second,row.first.first}]!=1) return false;
    return true;
}
inline bool verify(const Snapshot& s,const Identity& id,
                   const std::vector<std::array<int,3>>& facets,
                   const std::string& raw,const std::string& roles,
                   const std::string& materialKey,const std::string& material,
                   const std::string& producer)
{
    const int p=std::get<1>(id);
    if (!s.complete || !s.completedFluidOnlyPath || p<0 || p>=int(s.parents.size())
     || p>=int(s.valid.size()) || !s.valid[p]
     || s.parents[p].identity!=id || s.rawSha256!=raw || s.rolesDigest!=roles
     || s.materialKey!=materialKey || s.materialDigest!=material || s.producerDigest!=producer
     || facets.size()!=s.parents[p].facets.size() || !validShape(s,s.parents[p])) return false;
    for(std::size_t i=0;i<facets.size();++i) {
        const int f=s.parents[p].facets[i];
        if(f<0 || f>=int(s.faces.size()))return false;
        const auto& face=s.faces[f];
        if(facets[i]!=std::array<int,3>{{face.handle,face.patch,face.zone}})return false;
    }
    return true;
}
} // namespace
#endif
