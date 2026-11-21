#ifndef dcCartStarWarpedPhysicalBase_H
#define dcCartStarWarpedPhysicalBase_H
#include "dcCartExactBinary64Geometry.H"
#include <vector>
#include <array>
#include <functional>
namespace dcCartStarWarpedPhysicalBase
{
using Point=std::array<double,3>;
struct Result {bool known=false,supported=false;std::size_t operations=0;};
// Construction predicates only. The physical ring/points are never changed.
// The common face-centre fan is a proof device, not an inserted mesh point.
// Exact orthogonal projection uses the ACTUAL OF area direction. A simple
// projected ring and positive centre wedges certify its complete radial fan;
// strictly positive centre-edge-apex tets certify positive cone subvolumes.
// Existing parent kernel, edge closure, writer V/M and stock quality gates
// remain mandatory. Unknown/cap/clock/overflow retains the legacy refusal.
inline Result check(const std::vector<Point>& ring,const Point& centre,
    const Point& area,const Point& apex,const std::function<bool()>& available)
{
    namespace E=dcCartExactBinary64Geometry;
    Result result;
    if(ring.size()<3||ring.size()>128)return result;
    E::Context c(available,131072);
    const auto n=E::vector(area,c),fc=E::vector(centre,c),ap=E::vector(apex,c);
    const auto n2=E::dot(n,n,c);
    if(!c.good())return result;
    if(E::sign(n2)<=0){result.known=true;return result;}
    std::vector<E::Vector> q;q.reserve(ring.size());
    for(const auto& p:ring)q.push_back(E::vector(p,c));
    auto orientation=[&](std::size_t a,std::size_t b,std::size_t p)
    {
        return E::dot(E::cross(E::difference(q[b],q[a],c),
            E::difference(q[p],q[a],c),c),n,c);
    };
    auto between=[&](std::size_t a,std::size_t b,std::size_t p)
    {
        const auto u=E::difference(q[a],q[p],c),v=E::difference(q[b],q[p],c);
        // |n|^2 times the dot product of orthogonally projected u and v.
        // Polynomial degree four; equality includes exact projected contacts.
        const auto value=E::subtract(E::multiply(E::dot(u,v,c),n2,c),
            E::multiply(E::dot(u,n,c),E::dot(v,n,c),c),c);
        return E::sign(value)<=0;
    };
    for(std::size_t i=0;i<q.size();++i)
    {
        const auto edge=E::difference(q[(i+1)%q.size()],q[i],c);
        const auto fan=E::cross(edge,E::difference(fc,q[i],c),c);
        const auto wedge=E::dot(fan,n,c);
        const auto tetra=E::dot(fan,E::difference(fc,ap,c),c);
        if(!c.good()){result.operations=c.operations;return result;}
        if(E::sign(wedge)<=0||E::sign(tetra)<=0)
        {result.known=true;result.operations=c.operations;return result;}
    }
    for(std::size_t i=0;i<q.size();++i)for(std::size_t j=i+1;j<q.size();++j)
    {
        if(!c.take()){result.operations=c.operations;return result;}
        const auto in=(i+1)%q.size(),jn=(j+1)%q.size();
        if(in==j||jn==i)continue;
        const int a=E::sign(orientation(i,in,j)),b=E::sign(orientation(i,in,jn));
        const int d=E::sign(orientation(j,jn,i)),e=E::sign(orientation(j,jn,in));
        bool crossed=a*b<0&&d*e<0;
        // Non-adjacent projected endpoint contacts or overlaps are not simple.
        if(a==0)crossed=crossed||between(i,in,j);
        if(b==0)crossed=crossed||between(i,in,jn);
        if(d==0)crossed=crossed||between(j,jn,i);
        if(e==0)crossed=crossed||between(j,jn,in);
        if(!c.good()){result.operations=c.operations;return result;}
        if(crossed){result.known=true;result.operations=c.operations;return result;}
    }
    if(!c.take()){result.operations=c.operations;return result;}
    result.known=true;result.supported=true;result.operations=c.operations;return result;
}
}
#endif
