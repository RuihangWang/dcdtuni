#ifndef dcCartExactBinary64Geometry_H
#define dcCartExactBinary64Geometry_H
// Exact, bounded polynomial predicates only. Not source/coverage/quality authority.
#include "dcCartBoostBoundedMultiply.H"
#include <array>
#include <vector>
#include <map>
#include <functional>
#include <cstdint>
#include <cstring>
#include <cmath>
#include <limits>
#include <algorithm>
#include <stdexcept>
namespace dcCartExactBinary64Geometry
{
constexpr unsigned maxIntegerBits=16384;
constexpr int maxExponent=8192;
constexpr std::size_t maxFaces=2048,maxPoints=8192,maxFacetPoints=8,maxOperations=262144;
using Integer=boost::multiprecision::number<
    boost::multiprecision::cpp_int_backend<maxIntegerBits,maxIntegerBits,
    boost::multiprecision::signed_magnitude,boost::multiprecision::checked,void>,
    boost::multiprecision::et_off>;
using Point=std::array<double,3>;
using Face=std::vector<Point>;
using Faces=std::vector<Face>;
enum class Refusal { none,capacity,clock,nonfinite,exponent,integerOverflow,degenerate };
struct Context
{
    std::function<bool()> available;
    std::size_t operations=0,operationLimit=maxOperations;
    Refusal refusal=Refusal::none;
    explicit Context(std::function<bool()> clock,std::size_t limit=maxOperations)
      :available(std::move(clock)),operationLimit(std::min(limit,maxOperations)){}
    bool good()const{return refusal==Refusal::none;}
    bool fail(Refusal r){if(good())refusal=r;return false;}
    bool take()
    {
        if(!good())return false;
        if(operations>=std::min(operationLimit,maxOperations))return fail(Refusal::capacity);
        ++operations;
        try {if(!available||!available())return fail(Refusal::clock);}
        catch(...){return fail(Refusal::clock);}
        return true;
    }
};
struct Dyadic {Integer n=0;int exponent=0;unsigned degree=0;};
struct Sign {bool known=false;int value=0;};
struct Decision {bool known=false,value=false;};
struct Separation {bool known=false,usable=false,separated=false;};
struct Moments
{
    bool known=false;
    Dyadic sixV;
    std::array<Dyadic,3> twentyFourM1;
};
using Vector=std::array<Dyadic,3>;
inline unsigned bits(const Integer& n)
{
    if(n==0)return 0;
    const Integer p=n<0?-n:n;
    return unsigned(boost::multiprecision::msb(p)+1);
}
inline bool normalize(Dyadic& d,Context& c)
{
    if(!c.good())return false;
    if(d.degree>4)return c.fail(Refusal::capacity);
    if(d.n==0){d.exponent=0;return true;}
    const bool negative=d.n<0;Integer p=negative?-d.n:d.n;
    const unsigned z=unsigned(boost::multiprecision::lsb(p));
    if(d.exponent>maxExponent-int(z))return c.fail(Refusal::exponent);
    p>>=z;d.n=negative?-p:p;d.exponent+=int(z);
    if(d.exponent<-maxExponent||d.exponent>maxExponent||bits(d.n)>maxIntegerBits)
        return c.fail(Refusal::exponent);
    return true;
}
inline Dyadic number(double x,Context& c)
{
    Dyadic d;
    if(!c.take())return d;
    static_assert(sizeof(double)==8&&std::numeric_limits<double>::is_iec559,
        "exact predicates require IEEE binary64");
    std::uint64_t b=0;std::memcpy(&b,&x,8);
    const unsigned e=unsigned((b>>52)&2047);
    if(e==2047){c.fail(Refusal::nonfinite);return d;}
    const std::uint64_t m=b&((std::uint64_t(1)<<52)-1);
    d.n=e?m+(std::uint64_t(1)<<52):m;
    if(b>>63)d.n=-d.n;
    d.exponent=e?int(e)-1023-52:-1074;d.degree=1;
    normalize(d,c);return d; // signed zero is mathematical zero; alias bytes are separate.
}
inline Integer shift(const Integer& n,unsigned s)
{
    if(n<0)return -((-n)<<s);
    return n<<s;
}
inline bool bounded(const Dyadic& d,Context& c)
{
    if(d.exponent<-maxExponent||d.exponent>maxExponent)return c.fail(Refusal::exponent);
    if(d.degree>4)return c.fail(Refusal::capacity);
    return true;
}
inline Dyadic add(const Dyadic& a,const Dyadic& b,Context& c)
{
    Dyadic r;if(!c.take()||!bounded(a,c)||!bounded(b,c))return r;
    r.degree=std::max(a.degree,b.degree);
    r.exponent=std::min(a.exponent,b.exponent);
    const unsigned sa=unsigned(a.exponent-r.exponent),sb=unsigned(b.exponent-r.exponent);
    const unsigned ba=bits(a.n),bb=bits(b.n);
    if((ba&&ba+sa>=maxIntegerBits)||(bb&&bb+sb>=maxIntegerBits))
    {c.fail(Refusal::integerOverflow);return Dyadic();}
    try {r.n=shift(a.n,sa)+shift(b.n,sb);normalize(r,c);}
    catch(const std::exception&){c.fail(Refusal::integerOverflow);return Dyadic();}
    return c.good()?r:Dyadic();
}
inline Dyadic subtract(const Dyadic& a,const Dyadic& b,Context& c)
{Dyadic m=b;m.n=-m.n;return add(a,m,c);}
inline Dyadic multiply(const Dyadic& a,const Dyadic& b,Context& c)
{
    Dyadic r;if(!c.take()||!bounded(a,c)||!bounded(b,c))return r;
    if(a.degree+b.degree>4){c.fail(Refusal::capacity);return r;}
    r.degree=a.degree+b.degree;
    const long e=long(a.exponent)+long(b.exponent);
    if(e<-maxExponent||e>maxExponent){c.fail(Refusal::exponent);return r;}
    if(bits(a.n)+bits(b.n)>maxIntegerBits)
    {c.fail(Refusal::integerOverflow);return r;}
    try {r.exponent=int(e);r.n=a.n*b.n;normalize(r,c);}
    catch(const std::exception&){c.fail(Refusal::integerOverflow);return Dyadic();}
    return c.good()?r:Dyadic();
}
inline int sign(const Dyadic& d){return d.n==0?0:(d.n<0?-1:1);}
inline Vector vector(const Point& p,Context& c)
{Vector out;for(unsigned k=0;k<3;++k)out[k]=number(p[k],c);return out;}
inline Vector difference(const Vector& a,const Vector& b,Context& c)
{Vector r;for(unsigned k=0;k<3;++k)r[k]=subtract(a[k],b[k],c);return r;}
inline Vector cross(const Vector& a,const Vector& b,Context& c)
{
    Vector r;
    for(unsigned k=0;k<3;++k)r[k]=subtract(multiply(a[(k+1)%3],b[(k+2)%3],c),
        multiply(a[(k+2)%3],b[(k+1)%3],c),c);
    return r;
}
inline Dyadic dot(const Vector& a,const Vector& b,Context& c)
{
    Dyadic r;
    for(unsigned k=0;k<3;++k)r=add(r,multiply(a[k],b[k],c),c);
    return r;
}
inline Sign planeSide(const Point& a,const Point& b,const Point& d,const Point& p,Context& c)
{
    const Vector va=vector(a,c);
    const auto n=cross(difference(vector(b,c),va,c),difference(vector(d,c),va,c),c);
    const auto v=dot(n,difference(vector(p,c),va,c),c);
    return {c.good(),c.good()?sign(v):0};
}
inline bool validFaceCapacity(const Face& face,Context& c)
{
    if(face.size()<3||face.size()>maxFacetPoints)return c.fail(Refusal::capacity);
    return true;
}
inline bool facePlane(const Face& f,Vector& n,Vector& origin,Context& c)
{
    if(!validFaceCapacity(f,c)||!c.take())return false;
    origin=vector(f[0],c);
    for(std::size_t i=1;i+1<f.size();++i)
    {
        n=cross(difference(vector(f[i],c),origin,c),difference(vector(f[i+1],c),origin,c),c);
        if(!c.good())return false;
        if(sign(n[0])||sign(n[1])||sign(n[2]))return true;
    }
    return c.fail(Refusal::degenerate);
}
inline Decision planarConvex(const Face& f,Context& c)
{
    if(!validFaceCapacity(f,c)||!c.take())return {};
    // A strictly convex simple ring cannot visit a mathematical vertex twice.
    // Finite binary64 equality treats -0/+0 as one point, not as alias authority.
    for(const Point& p:f)
    {
        if(!c.take())return {};
        for(double coordinate:p)if(!std::isfinite(coordinate))
        {c.fail(Refusal::nonfinite);return {};}
    }
    for(std::size_t i=0;i<f.size();++i)for(std::size_t j=0;j<i;++j)
    {
        if(!c.take())return {};
        if(f[i]==f[j])return {true,false};
    }
    Vector n,o;
    if(!facePlane(f,n,o,c))return {};
    for(const Point& p:f)
        if(sign(dot(n,difference(vector(p,c),o,c),c))!=0)
            return {c.good(),false};
    for(std::size_t i=0;i<f.size();++i)
    {
        const Vector a=vector(f[i],c),b=vector(f[(i+1)%f.size()],c),d=vector(f[(i+2)%f.size()],c);
        if(sign(dot(cross(difference(b,a,c),difference(d,b,c),c),n,c))<=0)
            return {c.good(),false}; // strict turn; collinear/duplicate outline refuses.
    }
    // Consistent strict turns alone admit self-intersecting star polygons.
    // Every vertex must also be in every directed-edge inward halfspace.
    for(std::size_t i=0;i<f.size();++i)
    {
        const Vector a=vector(f[i],c),edge=difference(vector(f[(i+1)%f.size()],c),a,c);
        for(const Point& p:f)
        {
            const Dyadic side=dot(cross(edge,difference(vector(p,c),a,c),c),n,c);
            if(!c.good())return {};
            if(sign(side)<0)return {true,false};
        }
    }
    return {c.good(),c.good()};
}
inline bool validSurfaceCapacity(const Faces& f,Context& c)
{
    if(f.empty()||f.size()>maxFaces)return c.fail(Refusal::capacity);
    std::size_t count=0;
    for(const Face& ring:f)
    {
        if(ring.size()<3||ring.size()>maxFacetPoints||count>maxPoints-ring.size())
            return c.fail(Refusal::capacity);
        count+=ring.size();
    }
    return true; // capacity screen occurs before clocks/numeric traversal.
}
inline Decision shellHalfspaces(const Faces& f,Context& c)
{
    if(f.empty()||f.size()>64){c.fail(Refusal::capacity);return {};}
    if(!validSurfaceCapacity(f,c))return {};
    std::size_t vertices=0;for(const auto& ring:f)vertices+=ring.size();
    if(vertices>512){c.fail(Refusal::capacity);return {};}
    for(const Face& plane:f)
    {
        const Decision convex=planarConvex(plane,c);
        if(!convex.known||!convex.value)return convex;
        Vector n,o;if(!facePlane(plane,n,o,c))return {};
        for(const Face& ring:f)for(const Point& p:ring)
        {
            const Dyadic side=dot(n,difference(vector(p,c),o,c),c);
            if(!c.good())return {};
            if(sign(side)>0)return {true,false};
        }
    }
    return {c.good(),c.good()}; // allows mathematical support ties, not stock quality permission.
}
inline Moments polynomialMoments(const Faces& f,Context& c)
{
    Moments result;if(!validSurfaceCapacity(f,c))return result;
    for(const Face& ring:f)
    {
        if(!c.take())return Moments();
        const Decision planar=planarConvex(ring,c);
        if(!planar.known||!planar.value)return Moments();
        const Vector a=vector(ring[0],c);
        for(std::size_t i=1;i+1<ring.size();++i)
        {
            const Vector b=vector(ring[i],c),d=vector(ring[i+1],c);
            const Dyadic det=dot(a,cross(b,d,c),c);
            result.sixV=add(result.sixV,det,c);
            for(unsigned k=0;k<3;++k)
                result.twentyFourM1[k]=add(result.twentyFourM1[k],
                    multiply(det,add(add(a[k],b[k],c),d[k],c),c),c);
            if(!c.good())return Moments();
        }
    }
    result.known=c.good();return result;
}
inline Decision equalMoments(const Moments& a,const Moments& b,Context& c)
{
    if(!a.known||!b.known)return {};
    if(sign(subtract(a.sixV,b.sixV,c)))return {c.good(),false};
    for(unsigned k=0;k<3;++k)
        if(sign(subtract(a.twentyFourM1[k],b.twentyFourM1[k],c)))return {c.good(),false};
    return {c.good(),c.good()};
}
inline std::array<std::uint64_t,3> pointBits(const Point& p,Context& c)
{
    std::array<std::uint64_t,3> key{};
    for(unsigned k=0;k<3;++k)
    {
        if(!std::isfinite(p[k])){c.fail(Refusal::nonfinite);return {};}
        if(p[k]!=0)std::memcpy(&key[k],&p[k],8); // mathematical -0=+0; not writer alias permission.
    }
    return key;
}
inline Decision directedClosed(const Faces& f,Context& c)
{
    if(!validSurfaceCapacity(f,c))return {};
    using Edge=std::array<std::uint64_t,6>;
    std::map<Edge,std::pair<unsigned,int>> counts;
    try
    {
    for(const Face& ring:f)for(std::size_t i=0;i<ring.size();++i)
    {
        if(!c.take())return {};
        const auto a=pointBits(ring[i],c),b=pointBits(ring[(i+1)%ring.size()],c);
        if(!c.good())return {};
        if(a==b)return {true,false};
        Edge edge{};const bool forward=a<b;const auto& lo=forward?a:b;const auto& hi=forward?b:a;
        std::copy(lo.begin(),lo.end(),edge.begin());std::copy(hi.begin(),hi.end(),edge.begin()+3);
        auto& entry=counts[edge];++entry.first;entry.second+=forward?1:-1;
        if(entry.first>2)return {true,false};
    }
    for(const auto& e:counts)if(e.second.first!=2||e.second.second!=0)return {true,false};
    return {c.good(),c.good()};
    }
    catch(const std::bad_alloc&){c.fail(Refusal::capacity);return {};}
}
inline Separation separatedOnAxis(const std::vector<Point>& a,const std::vector<Point>& b,
    const Vector& axis,Context& c)
{
    if(a.empty()||b.empty()||a.size()>maxPoints||b.size()>maxPoints)
    {c.fail(Refusal::capacity);return {};}
    if(!c.good())return {};
    if(!sign(axis[0])&&!sign(axis[1])&&!sign(axis[2]))return {true,false,false};
    Dyadic amin,amax,bmin,bmax;bool first=true;
    for(const Point& p:a)
    {
        const Dyadic v=dot(vector(p,c),axis,c);
        if(!c.good())return {};
        if(first){amin=amax=v;first=false;}
        else {if(sign(subtract(v,amin,c))<0)amin=v;if(sign(subtract(v,amax,c))>0)amax=v;}
    }
    first=true;
    for(const Point& p:b)
    {
        const Dyadic v=dot(vector(p,c),axis,c);
        if(!c.good())return {};
        if(first){bmin=bmax=v;first=false;}
        else {if(sign(subtract(v,bmin,c))<0)bmin=v;if(sign(subtract(v,bmax,c))>0)bmax=v;}
    }
    const bool noPositiveOverlap=sign(subtract(amax,bmin,c))<=0||sign(subtract(bmax,amin,c))<=0;
    return {c.good(),c.good(),c.good()&&noPositiveOverlap};
}
inline Separation separatedByFace(const std::vector<Point>& a,const std::vector<Point>& b,
    const std::array<Point,3>& face,Context& c)
{
    // Caller capacities first, before axis evaluation.
    if(a.empty()||b.empty()||a.size()>maxPoints||b.size()>maxPoints)
    {c.fail(Refusal::capacity);return {};}
    const auto o=vector(face[0],c);
    const auto axis=cross(difference(vector(face[1],c),o,c),difference(vector(face[2],c),o,c),c);
    return separatedOnAxis(a,b,axis,c);
}
inline Separation separatedByEdges(const std::vector<Point>& a,const std::vector<Point>& b,
    const std::array<Point,2>& e1,const std::array<Point,2>& e2,Context& c)
{
    if(a.empty()||b.empty()||a.size()>maxPoints||b.size()>maxPoints)
    {c.fail(Refusal::capacity);return {};}
    const auto axis=cross(difference(vector(e1[1],c),vector(e1[0],c),c),
        difference(vector(e2[1],c),vector(e2[0],c),c),c);
    return separatedOnAxis(a,b,axis,c);
}
}
#endif
