#ifndef dcCartPreparedFacetPartition_H
#define dcCartPreparedFacetPartition_H
// Bounded prepared-geometry construction only. Never source/material/quality permission.
#include "../dcCartCutCells/dcCartExactBinary64Geometry.H"
#include "dcCartSignedCrossing.H"
#include <boost/rational.hpp>
#include <array>
#include <vector>
#include <map>
#include <unordered_map>
#include <set>
#include <string>
#include <tuple>
#include <functional>
#include <cstdint>
#include <cstring>
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <memory>
namespace dcCartPreparedFacetPartition
{
using Id=std::int64_t;
using Point=std::array<double,3>;
// Preserve the checked 16384-bit bound, allocating only the limbs in use.
using Integer=boost::multiprecision::number<boost::multiprecision::cpp_int_backend<
    512,16384,boost::multiprecision::signed_magnitude,boost::multiprecision::checked,
    std::allocator<boost::multiprecision::limb_type>>,boost::multiprecision::et_off>;
using Rational=boost::rational<Integer>;
using ExactPoint=std::array<Rational,3>;
enum class Role { original,source,curtain };
enum class CoreMaterial { solid,fluid };
struct NativeFacet
{
    Id surface=-1,shell=-1,triangle=-1,localRegion=-1,globalRegion=-1;
    std::array<Id,3> pointIds{{-1,-1,-1}};
    std::array<Point,3> p;Point normal{{0,0,0}};
    std::uint64_t geometryEpoch=0,membershipEpoch=0;
    std::string sourceObjectUid,sourceFaceUid,representation;
};
struct Window
{
    Id surface=-1,shell=-1;
    std::uint64_t geometryEpoch=0,membershipEpoch=0;
    Point requestedMin{{0,0,0}},requestedMax{{0,0,0}};
    bool complete=false;
    std::vector<NativeFacet> facets;
};
struct OriginalFace
{
    Id id=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    std::vector<Id> pointIds;std::vector<Point> points;
};
// Only a successfully committed same-shell Install may produce this cache.
// Stored mesh bytes stay unchanged; exact geometry is never reconstructed by
// rounding a previous construction a second time. This grants no material role.
struct OriginalExactPoint
{
    ExactPoint exactPoint;Point storedPoint{{0,0,0}};
    Id surface=-1,shell=-1;
    std::uint64_t geometryEpoch=0,membershipEpoch=0;
    bool operator==(const OriginalExactPoint& b)const
    {
        return exactPoint==b.exactPoint
          &&std::memcmp(storedPoint.data(),b.storedPoint.data(),3*sizeof(double))==0
          &&surface==b.surface&&shell==b.shell
          &&geometryEpoch==b.geometryEpoch&&membershipEpoch==b.membershipEpoch;
    }
    bool operator!=(const OriginalExactPoint& b)const{return !(*this==b);}
};
struct OriginalCell
{
    Id id=-1;
    std::vector<OriginalFace> faces;
    std::map<Id,int> pointSides; // actual current prepared-shell side labels, never guessed
    std::map<Id,OriginalExactPoint> exactPoints; // optional actual committed construction
};
struct Limits
{
    std::size_t facets=2048,points=8192,facesPerPart=2048,parts=2048,ring=256,steps=2000000;
};
struct Event
{
    enum class Kind { originalPoint,sourcePoint,sourceEdgeCellFace,sourceTriangleCellEdge,curtainCellFace,sourcePlaneCellFace,sourceEdgeLineCellFace,originalEdgeRefinement,originalFaceDiagonal };
    Kind kind=Kind::originalPoint;
    Id surface=-1,shell=-1,sourceTriangle=-1,originalFace=-1,originalPoint=-1;
    std::array<Id,2> sourceEdge{{-1,-1}},originalEdge{{-1,-1}};
    std::uint64_t geometryEpoch=0,membershipEpoch=0;
    Id constructionCell=-1,sourceCellFace=-1;
    bool operator<(const Event& b)const
    {
        return std::tie(kind,surface,shell,sourceTriangle,originalFace,originalPoint,sourceEdge,originalEdge,geometryEpoch,membershipEpoch,constructionCell,sourceCellFace)
          <std::tie(b.kind,b.surface,b.shell,b.sourceTriangle,b.originalFace,b.originalPoint,b.sourceEdge,b.originalEdge,b.geometryEpoch,b.membershipEpoch,b.constructionCell,b.sourceCellFace);
    }
};
struct Node { Point p;ExactPoint exactPoint;std::vector<Event> events; };
struct Face
{
    std::vector<std::size_t> ring;Role role=Role::original;
    Id originalFace=-1,sourceTriangle=-1,region=-1,zone=-1;
    // An internal curtain uses its actual prepared source edge. A boundary
    // curtain uses source-triangle x original-cell-face event; no coordinate UID.
    std::array<Id,2> sourceEdge{{-1,-1}};
    Id sourceCellFace=-1;std::vector<Id> sourceCellFaces;
};
struct Part
{
    std::vector<Face> faces;
    std::vector<Id> sourceTriangles;
    Point volumeCentroid{{0,0,0}},firstMoment{{0,0,0}};
    double volume=0,kernelMargin=0;
};
struct OriginalFacePlan
{
    Id originalFace=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    // The caller must apply this ONE plan to both actual incidences under its
    // complete-star transaction, then refine any further neighbouring plan.
    std::vector<std::pair<std::size_t,std::size_t>> fluidPartFaces;
    // Complete solid complement on this old face, including actual source contact.
    std::vector<std::size_t> solidPartFaces;
    // Multiple TRUE solid parts; the old single-part addresses remain empty.
    std::vector<std::pair<std::size_t,std::size_t>> solidPartFacePieces;
};
// Positive exact native-fragment intersection with ONE actual old-face polygon.
// Its orientation is the original native SOURCE winding (false may oppose old-face winding).
struct Contact
{
    Id sourceTriangle=-1,originalFace=-1,region=-1;
    std::vector<std::size_t> ring;
    bool solidOnCurrentCellSide=false;
};
struct OriginalPointContact
{
    Id originalPoint=-1,sourceTriangle=-1,surface=-1,shell=-1;
    int previousSide=0;
    std::uint64_t geometryEpoch=0,membershipEpoch=0;
};
struct Output
{
    Id originalCell=-1;
    std::vector<Node> nodes;std::vector<Part> fluidParts;
    Part solidPart;bool solidPartKnown=false;double solidKernelMargin=0;
    std::vector<Part> solidParts;bool solidPartsKnown=false;
    CoreMaterial coreMaterial=CoreMaterial::solid;
    Point corePoint{{0,0,0}};double coreMargin=0;
    std::vector<OriginalFacePlan> originalFacePlans;std::vector<Contact> contacts;
    // Explicit, verified source-boundary rebinding. Input old labels and point
    // bytes stay immutable; the caller commits target side 0 transactionally.
    std::vector<OriginalPointContact> originalPointContacts;
    std::size_t positiveSourceFragments=0,interiorSourceFragments=0,internalCurtainPairs=0;
    Point solidKernel{{0,0,0}};
    bool geometricKnown=false; // does not grant native/sourceReady/checkMesh acceptance
};
struct Input
{
    OriginalCell cell;Window window;
    Limits limits;
    std::function<bool()> available;
    // MUST bind the same actual prepared shell/material through its existing
    // native membership contract. Geometry below never invents this permission.
    std::function<bool(const Point&)> solidMembership;
    // Returns KNOWN through the held native material contract; false never
    // means fluid. isSolid is meaningful only when the callback returns true.
    std::function<bool(const Point&,bool& isSolid)> materialClassification;
    std::function<bool()> leaseHeld;
    bool mergeAdjacentSectors=false;
};
namespace detail
{
using ExactPoint=dcCartPreparedFacetPartition::ExactPoint;
struct Refusal:std::runtime_error { explicit Refusal(const char* s):std::runtime_error(s){} };
struct Context
{
    const Input& in;std::size_t steps=0;
    void tick()
    {
        if(++steps>in.limits.steps)throw Refusal("budget");
        bool ok=false;
        try {ok=in.available && in.available() && dcCartSignedCrossing::supportedFloatingEnvironment();}
        catch(...){throw Refusal("callback");}
        if(!ok)throw Refusal("clock-or-fp");
    }
};
inline bool finite(const Point& p){for(double x:p)if(!std::isfinite(x))return false;return true;}
inline Rational number(double x)
{
    std::uint64_t bits=0;std::memcpy(&bits,&x,8);
    const unsigned e=unsigned((bits>>52)&2047);
    if(e==2047)throw Refusal("nonfinite");
    Integer n=(bits&((std::uint64_t(1)<<52)-1));
    if(e)n+=(std::uint64_t(1)<<52);
    if(bits>>63)n=-n;
    int exponent=e?int(e)-1023-52:-1074;
    if(exponent>=0)
    {
        const bool negative=n<0;Integer magnitude=negative?-n:n;
        magnitude<<=unsigned(exponent);return Rational(negative?-magnitude:magnitude);
    }
    return Rational(n,Integer(1)<<unsigned(-exponent));
}
inline ExactPoint exact(const Point& p){return {{number(p[0]),number(p[1]),number(p[2])}};}
inline ExactPoint sub(const ExactPoint& a,const ExactPoint& b)
{ExactPoint r;for(unsigned d=0;d<3;++d)r[d]=a[d]-b[d];return r;}
inline ExactPoint add(const ExactPoint& a,const ExactPoint& b)
{ExactPoint r;for(unsigned d=0;d<3;++d)r[d]=a[d]+b[d];return r;}
inline ExactPoint mul(const ExactPoint& a,const Rational& t)
{ExactPoint r;for(unsigned d=0;d<3;++d)r[d]=a[d]*t;return r;}
inline Rational dot(const ExactPoint& a,const ExactPoint& b)
{Rational r;for(unsigned d=0;d<3;++d)r+=a[d]*b[d];return r;}
inline ExactPoint cross(const ExactPoint& a,const ExactPoint& b)
{return {{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}};}
inline bool nonzero(const ExactPoint& p){return p[0]!=0||p[1]!=0||p[2]!=0;}
inline double rounded(const Rational& r)
{
    if(r==0)return 0;
    const bool neg=r<0;Integer n=neg?-r.numerator():r.numerator(),d=r.denominator();
    int e=int(boost::multiprecision::msb(n))-int(boost::multiprecision::msb(d));
    if(e>=0){if(n<(d<<unsigned(e)))--e;}
    else if((n<<unsigned(-e))<d)--e;
    if(e>1023)throw Refusal("rounded-overflow");
    const int shift=e>=-1022?52-e:1074;
    Integer numerator=n,denominator=d;
    if(shift>=0)numerator<<=unsigned(shift);else denominator<<=unsigned(-shift);
    Integer q=numerator/denominator,remainder=numerator%denominator;
    const Integer twice=remainder<<1;
    if(twice>denominator||(twice==denominator&&(q&1)!=0))++q;
    std::uint64_t bits=0;
    if(e>=-1022)
    {
        if(q==(Integer(1)<<53)){q>>=1;++e;}
        if(e>1023)throw Refusal("rounded-overflow");
        const auto mantissa=q.convert_to<std::uint64_t>();
        bits=(std::uint64_t(e+1023)<<52)|(mantissa- (std::uint64_t(1)<<52));
    }
    else bits=q.convert_to<std::uint64_t>();
    if(neg)bits|=std::uint64_t(1)<<63;
    double x=0;std::memcpy(&x,&bits,8);
    if(!std::isfinite(x))throw Refusal("rounded-nonfinite");
    return x;
}
inline Point rounded(const ExactPoint& p){return {{rounded(p[0]),rounded(p[1]),rounded(p[2])}};}
using Ring=std::vector<ExactPoint>;
struct QFace
{
    Ring q;Role role=Role::original;Id originalFace=-1,sourceTriangle=-1,region=-1,zone=-1;std::size_t originalPiece=std::numeric_limits<std::size_t>::max();
    std::array<Id,2> sourceEdge{{-1,-1}};Id sourceCellFace=-1;std::vector<Id> sourceCellFaces;
};
using Polyhedron=std::vector<QFace>;
inline Ring clean(const Ring& q)
{
    Ring r;for(const auto& p:q)if(r.empty()||p!=r.back())r.push_back(p);
    if(r.size()>1&&r.front()==r.back())r.pop_back();
    return r;
}
inline ExactPoint area(const Ring& q)
{
    ExactPoint n{{Rational(0),Rational(0),Rational(0)}};
    for(std::size_t i=0;i<q.size();++i)n=add(n,cross(q[i],q[(i+1)%q.size()]));
    return mul(n,Rational(1,2));
}
inline Rational side(const ExactPoint& n,const ExactPoint& o,const ExactPoint& p)
{return dot(n,sub(p,o));}
// Predicate representation only: a positive scale preserves the cutting plane,
// side signs, exact intersections and cap orientation. Caller area vectors are
// untouched. If forming this representation exceeds checked precision, retain
// the original vector; genuine geometry/predicate failures are not caught here.
inline ExactPoint primitivePositiveNormal(const ExactPoint& n)
{
    try
    {
        Integer denominator=1;
        for(const auto& coordinate:n)
        {
            const Integer common=boost::integer::gcd(denominator,coordinate.denominator());
            denominator=(denominator/common)*coordinate.denominator();
        }
        std::array<Integer,3> scaled;Integer common=0;
        for(unsigned j=0;j<3;++j)
        {
            scaled[j]=n[j].numerator()*(denominator/n[j].denominator());
            const Integer magnitude=scaled[j]<0?-scaled[j]:scaled[j];
            common=boost::integer::gcd(common,magnitude);
        }
        if(common==0)return n;
        ExactPoint result;
        for(unsigned j=0;j<3;++j)result[j]=Rational(scaled[j]/common);
        return result;
    }
    catch(const std::overflow_error&){return n;}
}
// Numerical memoization for one immutable cutting plane only. Exact coordinates
// are arithmetic keys here, never point/source/material identities. First use
// performs the same bounded operation; a full cache falls back to that operation.
struct ExactPointSideHash
{
    std::size_t operator()(const ExactPoint& p)const
    {
        std::size_t seed=0;
        for(const auto& coordinate:p)
            seed^=std::hash<Rational>{}(coordinate)+std::size_t(0x9e3779b9)+(seed<<6)+(seed>>2);
        return seed;
    }
};
// The accelerator certifies strict signs only. It never supplies an
// approximate side value to a geometric intersection.
inline bool sideFilterFloatingEnvironment()
{
    // The caller's rounding and gradual-underflow requirements stay strict.
    // Exception traps are isolated only inside the optional interval filter.
    return dcCartSignedCrossing::supportedFloatingEnvironment();
}
inline bool sideFilterTrapsMasked()
{
#if defined(__x86_64__) && defined(__SSE__)
    unsigned short control=0;
    __asm__ __volatile__("fnstcw %0":"=m"(control));
    return (control&0x3fu)==0x3fu && (_mm_getcsr()&(0x3fu<<7))==(0x3fu<<7);
#else
    return false;
#endif
}
struct SideFilterEnvironment
{
    std::fenv_t saved;bool active=false;
    SideFilterEnvironment()
    {
        if(!sideFilterFloatingEnvironment()||std::fegetenv(&saved)!=0)return;
        active=true;std::fenv_t held;
        // Save before requesting nonstop mode, so even a declined request
        // restores the complete caller environment. Exact arithmetic, ticks
        // and geometric intersections execute after restore, outside this scope.
        if(std::feholdexcept(&held)!=0||!sideFilterFloatingEnvironment()||!sideFilterTrapsMasked())
            if(!restore())throw Refusal("side-filter-fp-restore");
    }
    SideFilterEnvironment(const SideFilterEnvironment&)=delete;
    SideFilterEnvironment& operator=(const SideFilterEnvironment&)=delete;
    bool restore()
    {
        if(!active)return true;
        if(std::fesetenv(&saved)!=0)return false;
        active=false;return true;
    }
    ~SideFilterEnvironment(){if(active)std::fesetenv(&saved);}
};
struct CutPlaneSideCache
{
    using Interval=dcCartSignedCrossing::detail::Interval;
    using Bounds=dcCartSignedCrossing::detail::IVector;
    struct Value
    {
        Rational exact=Rational(0);int sign=0;
        bool exactKnown=false,signKnown=false;
    };
    const ExactPoint& normal;const ExactPoint& origin;std::size_t limit;
    std::unordered_map<ExactPoint,Value,ExactPointSideHash> values;
    Bounds normalBounds,originBounds;bool planeBoundsReady=false;
    CutPlaneSideCache(const ExactPoint& n,const ExactPoint& o,std::size_t cap)
    :normal(n),origin(o),limit(cap){}
    static int exactSign(const Rational& r){return r<0?-1:(r>0?1:0);}
    static Interval bound(const Rational& r)
    {
        if(r==0)return dcCartSignedCrossing::detail::number(0);
        try
        {
            // d::rounded is the existing exact quotient/remainder nearest-even
            // conversion. Its immediate neighbours enclose the exact Rational.
            return dcCartSignedCrossing::detail::rounded(rounded(r));
        }
        catch(const Refusal& refusal)
        {
            if(std::strcmp(refusal.what(),"rounded-overflow")==0
              ||std::strcmp(refusal.what(),"rounded-nonfinite")==0)return {0,0,false};
            throw;
        }
        catch(const std::overflow_error&){return {0,0,false};}
    }
    static Bounds bound(const ExactPoint& p)
    {
        Bounds result;for(unsigned j=0;j<3;++j)result[j]=bound(p[j]);return result;
    }
    bool filteredSign(const ExactPoint& p,int& result)
    {
        SideFilterEnvironment environment;
        if(!environment.active)return false;
        int candidate=0;
        try
        {
            if(!planeBoundsReady)
            {
                normalBounds=bound(normal);originBounds=bound(origin);
                planeBoundsReady=true;
            }
            const Bounds pointBounds=bound(p);
            Bounds difference;
            for(unsigned j=0;j<3;++j)
                difference[j]=dcCartSignedCrossing::detail::sub(pointBounds[j],originBounds[j]);
            candidate=dcCartSignedCrossing::detail::strictSign(
                dcCartSignedCrossing::detail::dot(normalBounds,difference));
        }
        catch(...)
        {
            if(!environment.restore())throw Refusal("side-filter-fp-restore");
            throw;
        }
        if(!environment.restore())throw Refusal("side-filter-fp-restore");
        if(candidate==0)return false;
        result=candidate;return true;
    }
    Rational operator()(const ExactPoint& p)
    {
        const auto found=values.find(p);
        if(found!=values.end()&&found->second.exactKnown)return found->second.exact;
        // Exact fallback and geometric intersection values execute outside the
        // approximate fenv scope, with literal original coefficients/formula.
        const Rational value=side(normal,origin,p);
        if(found!=values.end())
        {
            found->second.exact=value;found->second.exactKnown=true;
            found->second.sign=exactSign(value);found->second.signKnown=true;
        }
        else if(values.size()<limit)
        {
            Value record;record.exact=value;record.exactKnown=true;
            record.sign=exactSign(value);record.signKnown=true;values.emplace(p,std::move(record));
        }
        return value;
    }
    int sign(const ExactPoint& p)
    {
        const auto found=values.find(p);
        if(found!=values.end()&&found->second.exactKnown)return found->second.sign;
        // Recheck after the caller's legacy tick/callback and before every
        // filtered cached use. Unsupported modes perform no approximation.
        if(!sideFilterFloatingEnvironment())return exactSign((*this)(p));
        if(found!=values.end()&&found->second.signKnown)return found->second.sign;
        int result=0;
        if(!filteredSign(p,result))return exactSign((*this)(p));
        if(found!=values.end()){found->second.sign=result;found->second.signKnown=true;}
        else if(values.size()<limit)
        {
            Value record;record.sign=result;record.signKnown=true;values.emplace(p,std::move(record));
        }
        return result;
    }
};
inline Ring clipRing(const Ring& q,const ExactPoint& n,const ExactPoint& o,Context& c,
    CutPlaneSideCache* sides=nullptr)
{
    Ring out;CutPlaneSideCache local(n,o,c.in.limits.points);
    CutPlaneSideCache& cache=sides?*sides:local;
    for(std::size_t i=0;i<q.size();++i)
    {
        c.tick();const auto& a=q[i];const auto& b=q[(i+1)%q.size()];
        const int sa=cache.sign(a),sb=cache.sign(b);
        if(sa<=0)out.push_back(a);
        if((sa<=0)!=(sb<=0))
        {
            const Rational da=cache(a),db=cache(b);
            out.push_back(add(a,mul(sub(b,a),da/(da-db))));
        }
    }
    out=clean(out);if(out.size()>c.in.limits.ring)throw Refusal("ring-cap");return out;
}
inline bool onSegment(const ExactPoint& p,const ExactPoint& a,const ExactPoint& b)
{
    for(unsigned j=0;j<3;++j)
    {
        const bool increasing=a[j]<b[j];
        const auto& lo=increasing?a[j]:b[j];
        const auto& hi=increasing?b[j]:a[j];
        if(p[j]<lo||p[j]>hi)return false;
    }
    const auto d=sub(b,a);if(!nonzero(d))return p==a;
    if(nonzero(cross(sub(p,a),d)))return false;
    for(unsigned j=0;j<3;++j)if(d[j]!=0){const auto t=(p[j]-a[j])/d[j];return t>=0&&t<=1;}
    return false;
}
inline Rational parameter(const ExactPoint& p,const ExactPoint& a,const ExactPoint& b)
{for(unsigned j=0;j<3;++j)if(b[j]!=a[j])return (p[j]-a[j])/(b[j]-a[j]);throw Refusal("zero-edge");}
inline Ring orderedCap(const std::set<ExactPoint>& set,const ExactPoint& n,Context& c)
{
    Ring q(set.begin(),set.end());if(q.size()<3)return {};
    unsigned drop=0;for(unsigned d=1;d<3;++d)if((n[d]<0?-n[d]:n[d])>(n[drop]<0?-n[drop]:n[drop]))drop=d;
    unsigned u=(drop==0?1:0),v=(drop==2?1:2);
    // The cut of a convex polyhedron is convex. Three non-collinear
    // vertices have an exact triangle centroid strictly inside that cap.
    // This preserves the complete cyclic boundary while avoiding the
    // denominator LCM of every boundary refinement point.
    if(!nonzero(n))throw Refusal("cap-zero-normal");
    std::size_t third=2;
    for(;third<q.size();++third)
    {
        c.tick();
        if(nonzero(cross(sub(q[1],q[0]),sub(q[third],q[0]))))break;
    }
    if(third==q.size())return {};
    for(const auto& p:q)
    {
        c.tick();
        if(side(n,q[0],p)!=0)throw Refusal("cap-points-nonplanar");
    }
    const ExactPoint mean=mul(add(add(q[0],q[1]),q[third]),Rational(1,3));
    auto half=[](const Rational& x,const Rational& y){return y>0||(y==0&&x>=0)?0:1;};
    std::sort(q.begin(),q.end(),[&](const ExactPoint& a,const ExactPoint& b)
    {
        const Rational ax=a[u]-mean[u],ay=a[v]-mean[v],bx=b[u]-mean[u],by=b[v]-mean[v];
        const int ha=half(ax,ay),hb=half(bx,by);if(ha!=hb)return ha<hb;
        const Rational z=ax*by-ay*bx;if(z!=0)return z>0;
        return ax*ax+ay*ay<bx*bx+by*by;
    });
    if(dot(area(q),n)<0)std::reverse(q.begin(),q.end());
    if(q.size()>c.in.limits.ring)throw Refusal("ring-cap");return clean(q);
}
inline Polyhedron cut(const Polyhedron& fs,const ExactPoint& n,const ExactPoint& o,QFace cap,Context& c)
{
    c.tick();bool in=false,out=false;CutPlaneSideCache sides(n,o,c.in.limits.points);
    const bool cacheFaces=fs.size()<=c.in.limits.facesPerPart;
    std::vector<unsigned char> faceHasPositive;
    if(cacheFaces)faceHasPositive.reserve(fs.size());
    for(const auto& f:fs)
    {
        bool positive=false;
        for(const auto& p:f.q)
        {const int sign=sides.sign(p);in=in||sign<0;out=out||sign>0;positive=positive||sign>0;}
        if(cacheFaces)faceHasPositive.push_back(positive);
    }
    if(!out)return fs;if(!in)return {};
    Polyhedron result;std::set<ExactPoint> points;std::size_t faceIndex=0;
    for(const auto& f:fs)
    {
        const bool fullyInside=cacheFaces&&!faceHasPositive[faceIndex];++faceIndex;
        Ring q;
        if(fullyInside)
        {
            // Same vertex ticks and ring normalization/cap checks as clipRing.
            // All first-scan signs are nonpositive, so no intersection is added.
            for(std::size_t i=0;i<f.q.size();++i)c.tick();
            q=clean(f.q);if(q.size()>c.in.limits.ring)throw Refusal("ring-cap");
        }
        else q=clipRing(f.q,n,o,c,&sides);
        if(q.size()<3||!nonzero(area(q)))continue;
        QFace piece=f;piece.q=std::move(q);result.push_back(std::move(piece));
        for(const auto& p:result.back().q)if(sides.sign(p)==0)points.insert(p);
    }
    cap.q=orderedCap(points,n,c);
    if(cap.q.size()>=3&&nonzero(area(cap.q)))result.push_back(std::move(cap));
    if(result.size()>c.in.limits.facesPerPart)throw Refusal("face-cap");
    return result;
}
inline bool closed(const Polyhedron& fs)
{
    std::map<std::pair<ExactPoint,ExactPoint>,std::size_t> uses;
    for(const auto& f:fs)for(std::size_t j=0;j<f.q.size();++j)++uses[{f.q[j],f.q[(j+1)%f.q.size()]}];
    for(const auto& use:uses)
    {const auto it=uses.find({use.first.second,use.first.first});if(use.second!=1||it==uses.end()||it->second!=1)return false;}
    return true;
}
struct Moments {Rational volume;ExactPoint moment{{Rational(0),Rational(0),Rational(0)}};};
// closedBoundary is only set after the caller's exact closed/closedChain
// certificate. Open single-face ear-partition integrals retain the original
// global formula. The dyadic reference changes integration coordinates only.
inline Moments moments(const Polyhedron& fs,Context& c,bool closedBoundary=false)
{
    Moments result;ExactPoint reference{{Rational(0),Rational(0),Rational(0)}};bool referenceReady=false;
    for(const auto& f:fs)for(std::size_t i=1;i+1<f.q.size();++i)
    {
        c.tick();
        if(!closedBoundary)
        {
            const auto& a=f.q[0];const auto& b=f.q[i];const auto& d=f.q[i+1];
            const Rational v=dot(a,cross(b,d))/6;
            result.volume+=v;result.moment=add(result.moment,mul(add(add(a,b),d),v/4));
        }
        else
        {
            // A binary64 round-trip avoids introducing a high-denominator
            // construction point as the common integration reference.
            if(!referenceReady){reference=exact(rounded(f.q[0]));referenceReady=true;}
            const auto a=sub(f.q[0],reference),b=sub(f.q[i],reference),d=sub(f.q[i+1],reference);
            const Rational v=dot(a,cross(b,d))/6;
            result.volume+=v;result.moment=add(result.moment,mul(add(add(a,b),d),v/4));
        }
    }
    if(closedBoundary&&referenceReady)result.moment=add(result.moment,mul(reference,result.volume));
    return result;
}
inline bool kernel(const Polyhedron& fs,const Point& candidate,double& minimum)
{
    const auto p=exact(candidate);minimum=std::numeric_limits<double>::infinity();
    for(const auto& f:fs)
    {
        const auto n=area(f.q);const auto s=side(n,f.q.front(),p);
        if(s>=0||!nonzero(n))return false;
        // Scaled normal avoids underflow/overflow; exact sign was checked first.
        const Point nf=rounded(n);double scale=0;for(double x:nf)scale=std::max(scale,std::abs(x));
        if(!(scale>0)||!std::isfinite(scale))return false;
        double sq=0;for(double x:nf)sq+=(x/scale)*(x/scale);
        const double distance=-rounded(s/number(scale))/std::sqrt(sq);
        if(!std::isfinite(distance))return false;minimum=std::min(minimum,distance);
    }
    return minimum>3e-6;
}
inline Ring canonical(const Ring& ring)
{
    Ring best;for(int direction:{1,-1})for(std::size_t start=0;start<ring.size();++start)
    {
        Ring q;for(std::size_t j=0;j<ring.size();++j)
        q.push_back(ring[(std::int64_t(start)+std::int64_t(direction)*std::int64_t(j)+std::int64_t(ring.size())*2)%ring.size()]);
        if(best.empty()||q<best)best=std::move(q);
    }
    return best;
}

// A cache is valid only for this immutable sector boundary and its group version.
// These arithmetic values never grant source/material/quality permission.
struct LowKernelSectorState
{
    Moments mm;double margin=0;bool hasKernel=false;std::size_t version=1;
};
struct LowKernelUnionStats
{
    std::size_t attempts=0,merges=0,removedCurtainPairs=0,unchangedPairSkips=0;
};
// The validated curtain table names original, immutable sector/face handles.
// Only true FLUID sectors may call this helper. All publication is staged.
template<class CurtainTable>
inline void repairLowKernelFluidSectors
(
    std::vector<Polyhedron>& sectors,std::vector<std::vector<Id>>& sources,
    const CurtainTable& curtains,Context& c,
    std::vector<LowKernelSectorState>& resultStates,LowKernelUnionStats& resultStats
)
{
    if(sectors.size()!=sources.size()||sectors.size()>c.in.limits.parts)
        throw Refusal("low-kernel-sector-address");
    std::vector<LowKernelSectorState> states(sectors.size());
    std::set<std::size_t> pending;
    for(std::size_t s=0;s<sectors.size();++s)
    {
        c.tick();if(sectors[s].empty())continue;
        if(!closed(sectors[s]))throw Refusal("output-part");
        auto& state=states[s];state.mm=moments(sectors[s],c,true);
        if(state.mm.volume<=0)throw Refusal("output-part");
        const auto centre=rounded(mul(state.mm.moment,Rational(1)/state.mm.volume));
        state.hasKernel=kernel(sectors[s],centre,state.margin);c.tick();
        if(!state.hasKernel)pending.insert(s);
    }
    LowKernelUnionStats stats;
    // Healthy geometry does not allocate the adjacency/face pool or change a ring.
    if(pending.empty()){resultStates.swap(states);resultStats=stats;return;}
    struct FaceRef {std::size_t sector,face;};
    struct Pair {FaceRef a,b;};
    struct Group
    {
        bool alive=true;std::vector<FaceRef> faces;std::vector<std::size_t> members;
        std::map<std::size_t,std::vector<std::size_t>> neighbours;
    };
    std::vector<Group> groups(sectors.size());std::vector<Pair> pairs;
    for(std::size_t s=0;s<sectors.size();++s)
    {
        c.tick();groups[s].alive=!sectors[s].empty();groups[s].members.push_back(s);
        for(std::size_t f=0;f<sectors[s].size();++f)
        {c.tick();groups[s].faces.push_back({s,f});}
    }
    auto reverseCycle=[&](const Ring& a,const Ring& b)
    {
        if(a.size()!=b.size()||a.empty())return false;
        for(std::size_t start=0;start<b.size();++start)
        {
            c.tick();if(a[0]!=b[start])continue;bool same=true;
            for(std::size_t j=0;j<a.size();++j)
            {c.tick();if(a[j]!=b[(start+b.size()-j)%b.size()]){same=false;break;}}
            if(same)return true;
        }
        return false;
    };
    std::set<std::pair<std::size_t,std::size_t>> seenCurtainFaces;
    for(const auto& entry:curtains)
    {
        c.tick();if(entry.second.size()!=2)throw Refusal("low-kernel-curtain-pair");
        const auto u=entry.second[0],v=entry.second[1];
        if(u.sector>=sectors.size()||v.sector>=sectors.size()||u.sector==v.sector
          ||u.face>=sectors[u.sector].size()||v.face>=sectors[v.sector].size())
            throw Refusal("low-kernel-curtain-address");
        if(!seenCurtainFaces.insert({u.sector,u.face}).second
          ||!seenCurtainFaces.insert({v.sector,v.face}).second)
            throw Refusal("low-kernel-duplicate-curtain-owner");
        const auto& a=sectors[u.sector][u.face];const auto& b=sectors[v.sector][v.face];
        if(a.role!=Role::curtain||b.role!=Role::curtain||a.q.size()<3
          ||!nonzero(area(a.q))||a.sourceEdge[0]<0||a.sourceEdge[1]<0||a.sourceEdge[0]==a.sourceEdge[1]
          ||a.sourceEdge!=b.sourceEdge||!reverseCycle(a.q,b.q)
          ||add(area(a.q),area(b.q))!=ExactPoint{{Rational(0),Rational(0),Rational(0)}})
            throw Refusal("low-kernel-curtain-identity");
        const auto index=pairs.size();pairs.push_back({{u.sector,u.face},{v.sector,v.face}});
        groups[u.sector].neighbours[v.sector].push_back(index);
        groups[v.sector].neighbours[u.sector].push_back(index);
    }
    for(std::size_t s=0;s<sectors.size();++s)
        for(std::size_t f=0;f<sectors[s].size();++f)
        {
            c.tick();if(sectors[s][f].role==Role::curtain&&!seenCurtainFaces.count({s,f}))
                throw Refusal("low-kernel-unpaired-curtain");
        }
    // Cache only complete, closed group moments. Each version-pair candidate
    // independently integrates its complete retained closed boundary once.
    using TrialKey=std::tuple<std::size_t,std::size_t,std::size_t,std::size_t>;
    std::set<TrialKey> tried;
    while(!pending.empty())
    {
        c.tick();const auto s=*pending.begin();pending.erase(pending.begin());
        if(!groups[s].alive||states[s].hasKernel)continue;
        std::vector<std::size_t> candidates;
        for(const auto& neighbour:groups[s].neighbours)
        {c.tick();candidates.push_back(neighbour.first);}
        for(const auto t:candidates)
        {
            c.tick();if(!groups[t].alive)throw Refusal("low-kernel-stale-adjacency");
            const auto u=std::min(s,t),v=std::max(s,t);
            const TrialKey key{u,states[u].version,v,states[v].version};
            if(!tried.insert(key).second){++stats.unchangedPairSkips;continue;}
            ++stats.attempts;
            const auto shared=groups[u].neighbours.find(v);
            const auto reverse=groups[v].neighbours.find(u);
            if(shared==groups[u].neighbours.end()||reverse==groups[v].neighbours.end()
              ||shared->second.empty()||shared->second!=reverse->second)
                throw Refusal("low-kernel-asymmetric-adjacency");
            std::set<std::pair<std::size_t,std::size_t>> removed;
            for(const auto id:shared->second)
            {
                c.tick();if(id>=pairs.size())throw Refusal("low-kernel-pair-address");
                const auto& pair=pairs[id];
                if(!removed.insert({pair.a.sector,pair.a.face}).second
                  ||!removed.insert({pair.b.sector,pair.b.face}).second)
                    throw Refusal("low-kernel-duplicate-curtain");
            }
            std::vector<FaceRef> refs;Polyhedron proposed;Moments after;
            auto append=[&](const Group& group)
            {
                for(const auto& ref:group.faces)
                {
                    c.tick();const auto& face=sectors[ref.sector][ref.face];
                    if(removed.erase({ref.sector,ref.face}))
                    {if(face.role!=Role::curtain)throw Refusal("low-kernel-physical-removal");continue;}
                    refs.push_back(ref);proposed.push_back(face);
                }
            };
            append(groups[u]);append(groups[v]);
            if(!removed.empty())throw Refusal("low-kernel-shared-face-missing");
            if(proposed.size()>c.in.limits.facesPerPart||!closed(proposed))continue;
            after=moments(proposed,c,true);
            if(after.volume<=0||after.volume!=states[u].mm.volume+states[v].mm.volume
              ||after.moment!=add(states[u].mm.moment,states[v].mm.moment))
                throw Refusal("low-kernel-union-moments");
            const auto centre=rounded(mul(after.moment,Rational(1)/after.volume));
            double margin=0;c.tick();const bool supported=kernel(proposed,centre,margin);c.tick();
            if(!supported)continue; // The true centroid, exact sign and3um gate are unchanged.
            const auto removedPairCount=shared->second.size();
            // Only neighbours of the two changed groups are touched.
            std::vector<std::size_t> touched;
            for(const auto& neighbour:groups[v].neighbours)
            {
                c.tick();const auto w=neighbour.first;if(w==u)continue;
                if(!groups[w].alive||!groups[w].neighbours.count(v))
                    throw Refusal("low-kernel-stale-peer");
                groups[w].neighbours.erase(v);
                auto& list=groups[u].neighbours[w];
                list.insert(list.end(),neighbour.second.begin(),neighbour.second.end());
                std::sort(list.begin(),list.end()); // Stable pair IDs, not coordinate authority.
                groups[w].neighbours[u]=list;touched.push_back(w);
            }
            groups[u].neighbours.erase(v);
            for(const auto& neighbour:groups[u].neighbours)
            {c.tick();touched.push_back(neighbour.first);}
            groups[u].faces.swap(refs);
            groups[u].members.insert(groups[u].members.end(),groups[v].members.begin(),groups[v].members.end());
            groups[v].alive=false;groups[v].faces.clear();groups[v].members.clear();groups[v].neighbours.clear();
            states[u].mm=std::move(after);states[u].margin=margin;states[u].hasKernel=true;++states[u].version;
            ++states[v].version;pending.erase(u);pending.erase(v);
            ++stats.merges;stats.removedCurtainPairs+=removedPairCount;
            for(const auto w:touched)
            {c.tick();if(!states[w].hasKernel)pending.insert(w);}
            break;
        }
    }
    for(std::size_t s=0;s<groups.size();++s)
    {c.tick();if(groups[s].alive&&!states[s].hasKernel)throw Refusal("output-kernel");}
    // No input sector/source mutation until all failing groups have a legal union.
    std::vector<Polyhedron> ready(sectors.size());std::vector<std::vector<Id>> readySources(sources.size());
    for(std::size_t s=0;s<groups.size();++s)if(groups[s].alive)
    {
        c.tick();for(const auto& ref:groups[s].faces)
        {c.tick();ready[s].push_back(sectors[ref.sector][ref.face]);}
        for(const auto member:groups[s].members)
        {c.tick();readySources[s].insert(readySources[s].end(),sources[member].begin(),sources[member].end());}
    }
    c.tick(); // Refuse an expired shared clock before publishing staged groups.
    sectors.swap(ready);sources.swap(readySources);resultStates.swap(states);resultStats=stats;
}

// Virtual exact convex partition of one simple planar old face. This only
// introduces internal diagonals between ACTUAL old vertices; it never moves,
// drops or invents a source point, nor changes the old face's region/identity.
inline Polyhedron convexOriginalPieces(const QFace& old,Context& c)
{
    const auto& q=old.q;const auto n=area(q);
    if(q.size()<3||!nonzero(n))throw Refusal("original-partition-zero-area");
    auto orient=[&](const ExactPoint& a,const ExactPoint& b,const ExactPoint& p)
    {return dot(cross(sub(b,a),sub(p,a)),n);};
    for(std::size_t i=0;i<q.size();++i)
    {
        if(q[i]==q[(i+1)%q.size()])throw Refusal("original-zero-edge");
        for(std::size_t j=i+1;j<q.size();++j)
        {
            if(j==i+1||(i==0&&j+1==q.size()))continue;c.tick();
            const auto& a=q[i];const auto& b=q[(i+1)%q.size()];
            const auto& p=q[j];const auto& z=q[(j+1)%q.size()];
            const Rational ap=orient(a,b,p),az=orient(a,b,z),pa=orient(p,z,a),pb=orient(p,z,b);
            if((ap==0&&onSegment(p,a,b))||(az==0&&onSegment(z,a,b))||
               (pa==0&&onSegment(a,p,z))||(pb==0&&onSegment(b,p,z))||
               ((ap<0)!=(az<0)&&ap!=0&&az!=0&&(pa<0)!=(pb<0)&&pa!=0&&pb!=0))
                throw Refusal("original-nonsimple-polygon");
        }
    }
    bool convex=true;
    for(std::size_t i=0;i<q.size();++i)
        if(orient(q[i],q[(i+1)%q.size()],q[(i+2)%q.size()])<0){convex=false;break;}
    if(convex)return {old};
    Ring remaining=q;Polyhedron result;ExactPoint sum{{Rational(0),Rational(0),Rational(0)}};
    while(remaining.size()>3)
    {
        bool found=false;
        for(std::size_t i=0;i<remaining.size();++i)
        {
            c.tick();const auto a=remaining[(i+remaining.size()-1)%remaining.size()],
                               b=remaining[i],z=remaining[(i+1)%remaining.size()];
            if(orient(a,b,z)<=0)continue;
            bool blocked=false;
            for(std::size_t j=0;j<remaining.size();++j)
            {
                if(j==i||j==(i+remaining.size()-1)%remaining.size()||j==(i+1)%remaining.size())continue;
                const auto& p=remaining[j];
                if(orient(a,b,p)>=0&&orient(b,z,p)>=0&&orient(z,a,p)>=0){blocked=true;break;}
            }
            if(blocked)continue;
            if(remaining.size()==4)
            {
                Ring rest;for(std::size_t j=0;j<remaining.size();++j)if(j!=i)rest.push_back(remaining[j]);
                if(dot(area(rest),n)<=0)continue;
            }
            QFace piece=old;piece.q={a,b,z};sum=add(sum,area(piece.q));result.push_back(std::move(piece));
            remaining.erase(remaining.begin()+i);found=true;break;
        }
        if(!found)throw Refusal("original-exact-ear-partition");
        if(result.size()>c.in.limits.facesPerPart)throw Refusal("original-partition-cap");
    }
    if(remaining.size()!=3||dot(area(remaining),n)<=0)throw Refusal("original-ear-final");
    QFace piece=old;piece.q=std::move(remaining);sum=add(sum,area(piece.q));result.push_back(std::move(piece));
    if(sum!=n)throw Refusal("original-partition-area");
    const auto before=moments({old},c),after=moments(result,c);
    if(before.volume!=after.volume||before.moment!=after.moment)throw Refusal("original-partition-moment");
    return result;
}

struct Fragment {QFace face;const NativeFacet* native=nullptr;Id contactFace=-1;bool solidOnCurrentCellSide=true;};
inline bool insideFace(const ExactPoint& p,const QFace& f,const ExactPoint& n)
{
    if(side(n,f.q.front(),p)!=0)return false;
    for(std::size_t i=0;i<f.q.size();++i)
    if(dot(cross(sub(f.q[(i+1)%f.q.size()],f.q[i]),sub(p,f.q[i])),n)<0)return false;
    return true;
}
inline bool insideFace(const ExactPoint& p,const QFace& f)
{return insideFace(p,f,area(f.q));}
template<class ImmutableOriginalArea>
inline bool boundarySegment(const ExactPoint& a,const ExactPoint& b,const Polyhedron& original,std::vector<Id>& faces,
    const ImmutableOriginalArea& originalArea)
{
    faces.clear();std::vector<std::pair<Rational,Rational>> intervals;
    for(const auto& f:original)
    {
        const auto& n=originalArea(f);
        if(side(n,f.q[0],a)!=0||side(n,f.q[0],b)!=0)continue;
        Rational lo(0),hi(1);const auto direction=sub(b,a);bool valid=true;
        for(std::size_t j=0;j<f.q.size();++j)
        {
            const auto x=f.q[j],edge=sub(f.q[(j+1)%f.q.size()],x);
            const auto start=dot(cross(edge,sub(a,x)),n),slope=dot(cross(edge,direction),n);
            if(slope==0){if(start<0){valid=false;break;}}
            else if(slope>0)lo=std::max(lo,-start/slope);else hi=std::min(hi,-start/slope);
            if(lo>hi){valid=false;break;}
        }
        if(valid&&lo<hi){intervals.push_back({lo,hi});faces.push_back(f.originalFace);}
    }
    std::sort(intervals.begin(),intervals.end());Rational end(0);
    for(const auto& interval:intervals){if(interval.first>end)return false;end=std::max(end,interval.second);}
    return end==1;
}
inline bool boundarySegment(const ExactPoint& a,const ExactPoint& b,const Polyhedron& original,std::vector<Id>& faces)
{
    return boundarySegment(a,b,original,faces,[](const QFace& f){return area(f.q);});
}
inline bool commonNativeEdge(const NativeFacet& f,const ExactPoint& a,const ExactPoint& b,std::array<Id,2>& out)
{
    for(unsigned j=0;j<3;++j)
    {
        const unsigned k=(j+1)%3;
        if(onSegment(a,exact(f.p[j]),exact(f.p[k]))&&onSegment(b,exact(f.p[j]),exact(f.p[k])))
        {out={{std::min(f.pointIds[j],f.pointIds[k]),std::max(f.pointIds[j],f.pointIds[k])}};return true;}
    }
    return false;
}
// Arithmetic memoization for one immutable fragment in one build. It carries
// no material/source permission and no construction-point-dependent value.
struct RadialFragmentMemo
{
    bool geometryReady=false;
    ExactPoint normal,centre;
    struct EdgeBinding
    {
        bool ready=false;
        std::array<Id,2> sourceEdge{{-1,-1}};
        Id sourceCellFace=-1;
        std::vector<Id> sourceCellFaces;
    };
    std::vector<EdgeBinding> edges;
};
inline void radialFragmentGeometry(const Fragment& fragment,RadialFragmentMemo& memo)
{
    if(memo.geometryReady)return;
    const auto& q=fragment.face.q;const auto normal=area(q);
    ExactPoint centre{{Rational(0),Rational(0),Rational(0)}};
    for(const auto& p:q)centre=add(centre,p);
    centre=mul(centre,Rational(1,Integer(q.size())));
    memo.normal=normal;memo.centre=std::move(centre);memo.geometryReady=true;
}
template<class ImmutableOriginalArea>
inline const RadialFragmentMemo::EdgeBinding& radialFragmentEdge
(
    const Fragment& fragment,std::size_t j,const Polyhedron& original,
    const ImmutableOriginalArea& originalArea,RadialFragmentMemo& memo
)
{
    const auto& q=fragment.face.q;
    if(memo.edges.empty())memo.edges.resize(q.size());
    auto& binding=memo.edges.at(j);
    if(!binding.ready)
    {
        QFace curtain;
        if(!commonNativeEdge(*fragment.native,q[j],q[(j+1)%q.size()],curtain.sourceEdge))
        {
            if(boundarySegment(q[j],q[(j+1)%q.size()],original,curtain.sourceCellFaces,originalArea))
                curtain.sourceCellFace=curtain.sourceCellFaces.front();
            if(curtain.sourceCellFace<0)throw Refusal("unbound-source-curtain");
        }
        binding.sourceEdge=curtain.sourceEdge;
        binding.sourceCellFace=curtain.sourceCellFace;
        binding.sourceCellFaces=std::move(curtain.sourceCellFaces);binding.ready=true;
    }
    return binding;
}
inline bool boxesOverlap(const Ring& a,const Ring& b)
{
    for(unsigned axis=0;axis<3;++axis)
    {
        Rational lo=a[0][axis],hi=lo,lb=b[0][axis],hb=lb;
        for(const auto& p:a){lo=std::min(lo,p[axis]);hi=std::max(hi,p[axis]);}
        for(const auto& p:b){lb=std::min(lb,p[axis]);hb=std::max(hb,p[axis]);}
        if(hi<lb||hb<lo)return false;
    }
    return true;
}
inline Ring faceIntersection(Ring a,const Ring& b,const ExactPoint& normal,Context& c)
{
    if(!boxesOverlap(a,b))return {};
    for(std::size_t j=0;j<b.size()&&!a.empty();++j)
        a=clipRing(a,cross(sub(b[(j+1)%b.size()],b[j]),normal),b[j],c);
    return a;
}
inline std::vector<Ring> faceDifference
(
    Ring a,const Ring& b,const ExactPoint& normal,Context& c
)
{
    if(!boxesOverlap(a,b))return {a};
    std::vector<Ring> out;
    for(std::size_t j=0;j<b.size()&&!a.empty();++j)
    {
        const auto n=cross(sub(b[(j+1)%b.size()],b[j]),normal);
        Ring outside=clipRing(a,mul(n,Rational(-1)),b[j],c);
        if(outside.size()>=3&&nonzero(area(outside)))out.push_back(std::move(outside));
        a=clipRing(a,n,b[j],c);
    }
    return out;
}
// One call-local immutable ring lease. Bounds/planes are evaluated lazily
// in the same axis/edge order as the uncached operations. No rounded test,
// tick elision, ring mutation or external/source identity is involved.
struct ImmutableRingCache
{
    const Ring& ring;const ExactPoint& normal;
    std::array<bool,3> boundKnown{{false,false,false}};
    std::array<Rational,3> lower,upper;
    std::vector<ExactPoint> planes;std::vector<bool> planeKnown;
    ImmutableRingCache(const Ring& q,const ExactPoint& n)
    :ring(q),normal(n),planes(q.size()),planeKnown(q.size(),false){}
    void bounds(unsigned axis)
    {
        if(boundKnown[axis])return;
        Rational lo=ring[0][axis],hi=lo;
        for(const auto& p:ring){lo=std::min(lo,p[axis]);hi=std::max(hi,p[axis]);}
        lower[axis]=std::move(lo);upper[axis]=std::move(hi);boundKnown[axis]=true;
    }
    const ExactPoint& plane(std::size_t j,bool primitive=false)
    {
        if(!planeKnown[j])
        {
            planes[j]=cross(sub(ring[(j+1)%ring.size()],ring[j]),normal);
            if(primitive)planes[j]=primitivePositiveNormal(planes[j]);
            planeKnown[j]=true;
        }
        return planes[j];
    }
};
inline bool cachedBoxesOverlap(const Ring& a,ImmutableRingCache& b,ImmutableRingCache* aCache=nullptr)
{
    for(unsigned axis=0;axis<3;++axis)
    {
        Rational lo,hi;
        if(aCache)
        {
            aCache->bounds(axis);lo=aCache->lower[axis];hi=aCache->upper[axis];
        }
        else
        {
            lo=a[0][axis];hi=lo;
            for(const auto& p:a){lo=std::min(lo,p[axis]);hi=std::max(hi,p[axis]);}
        }
        b.bounds(axis);
        if(hi<b.lower[axis]||b.upper[axis]<lo)return false;
    }
    return true;
}
inline Ring cachedFaceIntersection(Ring a,ImmutableRingCache& b,Context& c,ImmutableRingCache* aCache=nullptr,bool primitivePlanes=false)
{
    if(!cachedBoxesOverlap(a,b,aCache))return {};
    for(std::size_t j=0;j<b.ring.size()&&!a.empty();++j)
        a=clipRing(a,b.plane(j,primitivePlanes),b.ring[j],c);
    return a;
}
inline std::vector<Ring> cachedFaceDifference(Ring a,ImmutableRingCache& b,Context& c,bool primitivePlanes=false)
{
    if(!cachedBoxesOverlap(a,b))return {a};
    std::vector<Ring> out;
    for(std::size_t j=0;j<b.ring.size()&&!a.empty();++j)
    {
        const auto& n=b.plane(j,primitivePlanes);
        Ring outside=clipRing(a,mul(n,Rational(-1)),b.ring[j],c);
        if(outside.size()>=3&&nonzero(area(outside)))out.push_back(std::move(outside));
        a=clipRing(a,n,b.ring[j],c);
    }
    return out;
}
// Split a true coplanar native fragment against ACTUAL old-face polygons.
// Supporting-plane equality alone is not contact incidence. Every positive
// contact has one real old-face ID; their interiors must be disjoint and their
// exact area must cover the complete fragment. The original source fragment
// remains intact for native source-edge topology and radial construction.
template<class OriginalArea>
inline std::vector<Fragment> coplanarContactsWithAreas
(
    Fragment& fragment,const Polyhedron& original,Context& c,OriginalArea originalArea
)
{
    std::vector<Fragment> contacts;
    const auto sourceNormal=area(fragment.face.q);bool supportingPlane=false;
    ExactPoint total{{Rational(0),Rational(0),Rational(0)}};
    for(const auto& old:original)
    {
        c.tick();const auto normal=originalArea(old);bool on=true;
        for(const auto& p:fragment.face.q)if(side(normal,old.q[0],p)!=0){on=false;break;}
        if(!on)continue;supportingPlane=true;
        const Rational orientation=dot(normal,sourceNormal);
        if(orientation==0)throw Refusal("coplanar-contact-zero-orientation");
        Ring aligned=fragment.face.q;
        if(orientation<0)std::reverse(aligned.begin(),aligned.end());
        Ring q=faceIntersection(std::move(aligned),old.q,normal,c);
        if(q.size()<3||!nonzero(area(q)))continue;
        if(orientation<0)std::reverse(q.begin(),q.end());
        for(const auto& prior:contacts)
        {
            c.tick();const auto overlap=faceIntersection(q,prior.face.q,sourceNormal,c);
            if(overlap.size()>=3&&nonzero(area(overlap)))
                throw Refusal("coplanar-contact-multiple-cover");
            if(prior.solidOnCurrentCellSide!=(orientation>0))
                throw Refusal("coplanar-contact-inconsistent-side");
        }
        Fragment piece=fragment;piece.face.q=std::move(q);
        piece.contactFace=old.originalFace;piece.face.originalPiece=old.originalPiece;piece.solidOnCurrentCellSide=orientation>0;
        total=add(total,area(piece.face.q));contacts.push_back(std::move(piece));
        if(contacts.size()>c.in.limits.facesPerPart)throw Refusal("coplanar-contact-cap");
    }
    if(!supportingPlane)return contacts;
    if(contacts.empty()||total!=sourceNormal)
        throw Refusal("coplanar-contact-incomplete-cover");
    fragment.contactFace=-2; // true contact, possibly split across multiple real old faces
    fragment.solidOnCurrentCellSide=contacts.front().solidOnCurrentCellSide;
    return contacts;
}

inline std::vector<Fragment> coplanarContacts
(
    Fragment& fragment,const Polyhedron& original,Context& c
)
{
    return coplanarContactsWithAreas(fragment,original,c,
        [](const QFace& old){return area(old.q);});
}

// Exact oriented boundary-chain closure, with subdivision represented as
// intervals on the SAME exact 3D line. This accepts hanging nodes algebraically;
// the caller still must propagate them through every real incidence before commit.
inline bool closedChain(const Polyhedron& faces,Context& c)
{
    using Line=std::pair<ExactPoint,ExactPoint>;
    std::map<Line,std::map<Rational,long>> events;
    for(const auto& face:faces)for(std::size_t i=0;i<face.q.size();++i)
    {
        c.tick();const auto& a=face.q[i];const auto& b=face.q[(i+1)%face.q.size()];
        auto direction=sub(b,a);unsigned axis=0;while(axis<3&&direction[axis]==0)++axis;
        if(axis==3)return false;
        direction=mul(direction,Rational(1)/direction[axis]);
        const auto origin=sub(a,mul(direction,a[axis]));
        auto& line=events[{direction,origin}];
        ++line[a[axis]];--line[b[axis]];
    }
    for(const auto& line:events)
    {long winding=0;for(const auto& event:line.second){winding+=event.second;if(winding!=0)return false;}}
    return true;
}
inline bool classifiedMaterial(const Point& p,bool& isSolid,Context& c)
{
    c.tick();
    if(!c.in.materialClassification||!c.in.leaseHeld||!c.in.leaseHeld())
        throw Refusal("material-classification-unresolved");
    bool known=false;
    try {known=c.in.materialClassification(p,isSolid);}
    catch(...) {throw Refusal("material-classification-callback");}
    c.tick();
    if(!known||!c.in.leaseHeld())throw Refusal("material-classification-unresolved");
    return isSolid;
}
inline Polyhedron fullCoverage
(
    const Polyhedron& original,const std::vector<Fragment>& fragments,
    const std::vector<Fragment>& contacts,const std::vector<Polyhedron>& sectors,const std::map<Id,ExactPoint>& points,
    bool sectorsAreSolid,std::vector<Id>& contactCorrections,Context& c
)
{
    // A common radial apex gives each sector an interval reaching an actual
    // outer face. Positive overlap there is a duplicate radial crossing.
    Polyhedron solid;
    for(const auto& old:original)
    {
        c.tick();const auto normal=area(old.q);unsigned axis=0;while(axis<3&&normal[axis]==0)++axis;
        const ExactPoint predicateNormal=primitivePositiveNormal(normal);
        std::vector<const QFace*> fluid;
        for(const auto& sector:sectors)for(const auto& face:sector)
            if(face.role==Role::original&&face.originalFace==old.originalFace&&face.originalPiece==old.originalPiece)fluid.push_back(&face);
        // Each pointer refers to an unchanged face in const sectors for the
        // lifetime of this original-face iteration. Caches never escape it.
        std::vector<ImmutableRingCache> fluidCaches;fluidCaches.reserve(fluid.size());
        for(const auto* face:fluid)fluidCaches.emplace_back(face->q,predicateNormal);
        Rational fluidArea=0;
        for(std::size_t i=0;i<fluid.size();++i)
        {
            fluidArea+=area(fluid[i]->q)[axis];
            for(std::size_t j=i+1;j<fluid.size();++j)
            {
                c.tick();if(!cachedBoxesOverlap(fluid[i]->q,fluidCaches[j],&fluidCaches[i]))continue;
                const auto q=cachedFaceIntersection(fluid[i]->q,fluidCaches[j],c,&fluidCaches[i],true);
                if(q.size()>=3&&nonzero(area(q)))throw Refusal("fluid-original-face-multiple-cover");
            }
        }
        std::vector<Ring> remaining{old.q};
        for(std::size_t faceIndex=0;faceIndex<fluid.size();++faceIndex)
        {
            std::vector<Ring> next;
            for(const auto& ring:remaining)
            {
                auto pieces=cachedFaceDifference(ring,fluidCaches[faceIndex],c,true);
                for(auto& piece:pieces)next.push_back(std::move(piece));
                if(next.size()>c.in.limits.facesPerPart)throw Refusal("original-arrangement-cap");
            }
            remaining.swap(next);
        }
        Rational remainingArea=0;for(const auto& q:remaining)remainingArea+=area(q)[axis];
        if(fluidArea+remainingArea!=normal[axis])throw Refusal("original-arrangement-area");
        // A true source-contact replaces its portion of the original solid
        // boundary exactly once, retaining the source triangle/region account.
        for(const auto& fragment:contacts)if(fragment.contactFace==old.originalFace&&fragment.face.originalPiece==old.originalPiece)
        {
            // True means solid lies inside this cell; false is a source
            // contact with solid OUTSIDE. The latter must already belong to
            // this cell's fluid original-face coverage, never its solid core.
            Ring contact=fragment.face.q;
            if(dot(area(contact),normal)<0)std::reverse(contact.begin(),contact.end());
            Rational covered=0;
            if(fragment.solidOnCurrentCellSide)
            {
                for(const auto& ring:remaining)
                {
                    const auto q=faceIntersection(contact,ring,normal,c);
                    if(q.size()>=3&&nonzero(area(q)))covered+=area(q)[axis];
                }
            }
            else
            {
                for(std::size_t faceIndex=0;faceIndex<fluid.size();++faceIndex)
                {
                    const auto q=cachedFaceIntersection(contact,fluidCaches[faceIndex],c);
                    if(q.size()>=3&&nonzero(area(q)))covered+=area(q)[axis];
                }
            }
            if(covered!=area(contact)[axis])throw Refusal("contact-side-coverage");
            if(!fragment.solidOnCurrentCellSide)continue;
            ImmutableRingCache contactCache(contact,normal);
            std::vector<Ring> next;
            for(const auto& ring:remaining)
            {
                auto pieces=cachedFaceDifference(ring,contactCache,c);
                for(auto& piece:pieces)next.push_back(std::move(piece));
                if(next.size()>c.in.limits.facesPerPart)throw Refusal("contact-arrangement-cap");
            }
            remaining.swap(next);
        }
        for(auto& q:remaining)
        {QFace f=old;f.q=std::move(q);solid.push_back(std::move(f));}
    }
    for(const auto& fragment:fragments)if(fragment.contactFace==-1)
        solid.push_back(fragment.face);
    for(const auto& contact:contacts)if(contact.solidOnCurrentCellSide)
    {QFace face=contact.face;face.originalFace=contact.contactFace;solid.push_back(std::move(face));}
    if(solid.size()>c.in.limits.facesPerPart||!closedChain(solid,c))
        throw Refusal("solid-independent-boundary-closure");
    const auto originalMoments=moments(original,c,true),solidMoments=moments(solid,c,true);
    Moments fluidMoments;
    for(const auto& sector:sectors)
    {
        const auto m=moments(sector,c,true);
        fluidMoments.volume+=m.volume;fluidMoments.moment=add(fluidMoments.moment,m.moment);
    }
    if(solidMoments.volume<=0||fluidMoments.volume<=0
      ||fluidMoments.volume+solidMoments.volume!=originalMoments.volume
      ||add(fluidMoments.moment,solidMoments.moment)!=originalMoments.moment)
        throw Refusal("independent-volume-first-moment");
    // Bind the classification to every REAL original point-side entry. Exact
    // source contact is distinguished from the native ON band, never repaired.
    // These fragment/sector faces are immutable for this function's lifetime.
    // Compute each literal exact area lazily in its original first-use order.
    std::map<const QFace*,ExactPoint> classificationAreas;
    auto classificationArea=[&](const QFace& face)->const ExactPoint&
    {
        const auto found=classificationAreas.find(&face);
        if(found!=classificationAreas.end())return found->second;
        return classificationAreas.emplace(&face,primitivePositiveNormal(area(face.q))).first->second;
    };
    for(const auto& point:points)
    {
        c.tick();bool contact=false,fluid=false;
        for(const auto& fragment:fragments)if(insideFace(point.second,fragment.face,classificationArea(fragment.face))){contact=true;break;}
        if(!contact)for(const auto& sector:sectors)
        {
            bool inside=true;for(const auto& f:sector)if(side(classificationArea(f),f.q[0],point.second)>0){inside=false;break;}
            if(inside){fluid=true;break;}
        }
        const int actual=c.in.cell.pointSides.at(point.first);
        const int expected=contact?0:(fluid?(sectorsAreSolid?-1:1):(sectorsAreSolid?1:-1));
        if(actual!=expected)
        {
            // Actual 0 is the legacy ON band, not an exact source-contact
            // certificate. Its immutable label is retained; fresh known
            // material must independently agree with the exact arrangement.
            if(contact&&actual!=0)
            {
                // A positive exact prepared fragment already proves source
                // boundary incidence. Defer emitting its typed certificate
                // until that actual triangle and node are present in output.
                contactCorrections.push_back(point.first);continue;
            }
            if(actual!=0||contact)throw Refusal("original-point-side-geometry-mismatch");
            bool isSolid=false;
            if(classifiedMaterial(rounded(point.second),isSolid,c)!=(expected<0))
                throw Refusal("original-point-side-geometry-mismatch");
        }
    }
    return solid;
}

// Use only after build/caller has checked the record's stored bytes and lease.
inline ExactPoint originalExactPoint(const OriginalCell& cell,Id id,const Point& stored)
{
    const auto found=cell.exactPoints.find(id);
    return found==cell.exactPoints.end()?exact(stored):found->second.exactPoint;
}

inline bool bytesEqual(const Point& a,const Point& b){return std::memcmp(a.data(),b.data(),3*sizeof(double))==0;}
} // detail

// All output is staged. The caller retains material, full-star, budget, source,
// coupled-peer, writer and strict stock-checkMesh gates. false leaves out empty.
inline bool build(const Input& in,Output& out,std::string* refusal=nullptr)
{
    out=Output();
    if(refusal){try{refusal->clear();}catch(...){}}
    try
    {
        namespace d=detail;d::Context c{in};c.tick();
        if(!in.leaseHeld||!in.leaseHeld())throw d::Refusal("lease");
        c.tick();
        if(!in.window.complete||in.window.surface<0||in.window.shell<0
          ||!in.window.geometryEpoch||in.window.geometryEpoch!=in.window.membershipEpoch
          ||in.window.facets.empty()||in.window.facets.size()>in.limits.facets
          ||in.cell.id<0||in.cell.faces.size()<4||in.cell.faces.size()>in.limits.facesPerPart)
            throw d::Refusal("input");
        if(!d::finite(in.window.requestedMin)||!d::finite(in.window.requestedMax))
            throw d::Refusal("coverage-box-nonfinite");
        for(unsigned axis=0;axis<3;++axis)
            if(in.window.requestedMin[axis]>in.window.requestedMax[axis])
                throw d::Refusal("coverage-box-order");
        if(in.cell.exactPoints.size()>in.limits.points)throw d::Refusal("original-exact-cap");
        for(const auto& entry:in.cell.exactPoints)
        {
            c.tick();const auto& record=entry.second;
            if(entry.first<0||record.surface!=in.window.surface||record.shell!=in.window.shell
              ||record.geometryEpoch!=in.window.geometryEpoch
              ||record.membershipEpoch!=in.window.membershipEpoch)
                throw d::Refusal("original-exact-lease");
            if(!d::finite(record.storedPoint)
              ||!d::bytesEqual(d::rounded(record.exactPoint),record.storedPoint))
                throw d::Refusal("original-exact-rounding");
        }
        const auto exactBoxMin=d::exact(in.window.requestedMin),exactBoxMax=d::exact(in.window.requestedMax);
        d::Polyhedron original;std::map<Id,d::ExactPoint> originalPoints;
        std::map<Id,const OriginalFace*> originalById;
        std::map<Id,d::ExactPoint> sourcePoints;std::map<Id,Point> sourceBytes;
        for(const auto& f:in.cell.faces)
        {
            c.tick();
            if(f.id<0||f.owner<0||f.neighbour<-1||f.owner==f.neighbour
              ||f.patch<-1||f.zone<-1||(f.owner!=in.cell.id&&f.neighbour!=in.cell.id)
              ||f.points.size()!=f.pointIds.size()||f.points.size()<3||f.points.size()>in.limits.ring
              ||!originalById.emplace(f.id,&f).second)throw d::Refusal("cell-face");
            d::QFace q;q.originalFace=f.id;q.region=f.patch;q.zone=f.zone;
            std::set<Id> unique;
            for(std::size_t j=0;j<f.points.size();++j)
            {
                const auto id=f.pointIds[j];if(id<0||!unique.insert(id).second||!d::finite(f.points[j]))throw d::Refusal("cell-point");
                for(unsigned a=0;a<3;++a)if(f.points[j][a]<in.window.requestedMin[a]||f.points[j][a]>in.window.requestedMax[a])throw d::Refusal("coverage-box");
                const auto side=in.cell.pointSides.find(id);
                if(side==in.cell.pointSides.end()||side->second<-1||side->second>1)
                    throw d::Refusal("original-point-side");
                const auto record=in.cell.exactPoints.find(id);
                if(record!=in.cell.exactPoints.end()
                  &&!d::bytesEqual(record->second.storedPoint,f.points[j]))
                    throw d::Refusal("original-exact-stored-bits");
                const auto p=d::originalExactPoint(in.cell,id,f.points[j]);
                for(unsigned a=0;a<3;++a)if(p[a]<exactBoxMin[a]||p[a]>exactBoxMax[a])
                    throw d::Refusal("original-exact-coverage-box");
                const auto it=originalPoints.find(id);
                if(it!=originalPoints.end()&&it->second!=p)throw d::Refusal("original-id");
                originalPoints[id]=p;q.q.push_back(p);
            }
            if(f.owner!=in.cell.id)std::reverse(q.q.begin(),q.q.end());
            const auto n=d::area(q.q);if(!d::nonzero(n))throw d::Refusal("zero-original-face");
            for(const auto& p:q.q)if(d::side(n,q.q[0],p)!=0)throw d::Refusal("nonplanar-original");
            original.push_back(std::move(q));
        }
        for(const auto& entry:in.cell.exactPoints)
            if(!originalPoints.count(entry.first))throw d::Refusal("original-exact-unused");
        if(originalPoints.size()>in.limits.points||!d::closed(original))throw d::Refusal("original-closure");
        // Lazy predicate normal only: true area/metric computations stay separate.
        // Preserve the first actual area use and
        // retire every entry when the virtual original face vector is swapped.
        std::map<const d::QFace*,d::ExactPoint> originalPredicateNormalCache;
        const auto originalPredicateNormal=[&](const d::QFace& face)->const d::ExactPoint&
        {
            const auto found=originalPredicateNormalCache.find(&face);
            if(found!=originalPredicateNormalCache.end())return found->second;
            return originalPredicateNormalCache.emplace(&face,d::primitivePositiveNormal(d::area(face.q))).first->second;
        };
        using PredicatePlane=std::pair<d::ExactPoint,Rational>;
        const auto firstPredicatePlane=[&](const d::QFace& face,
            std::set<PredicatePlane>& planes)->bool
        {
            const auto& normal=originalPredicateNormal(face);
            try
            {
                // Positive primitive normal preserves halfspace orientation.
                // Overflow of this optional key alone falls back to the old work.
                const PredicatePlane key{normal,d::dot(normal,face.q.front())};
                return planes.insert(key).second;
            }
            catch(const std::overflow_error&){return true;}
        };
        std::set<PredicatePlane> convexityPlanes;
        for(const auto& f:original)
        {
            if(!firstPredicatePlane(f,convexityPlanes))continue;
            for(const auto& p:originalPoints)
                if(d::side(originalPredicateNormal(f),f.q[0],p.second)>0)throw d::Refusal("nonconvex-original");
        }
        // The cell itself remains the same exact convex closed boundary.
        // Individual old face polygons may be concave after earlier source
        // arrangements; halfspace-clipping each of those would create a false
        // coverage hole. Keep their actual identity and partition them virtually.
        d::Polyhedron convexOriginal;
        std::map<Id,std::set<std::array<Id,2>>> originalDiagonals;
        for(const auto& old:original)
        {
            const auto pieces=d::convexOriginalPieces(old,c);
            for(auto piece:pieces)
            {
                piece.originalPiece=convexOriginal.size();
                if(pieces.size()>1)
                {
                    const auto& real=*originalById.at(old.originalFace);
                    for(std::size_t k=0;k<piece.q.size();++k)
                    {
                        const auto& a=piece.q[k];const auto& b=piece.q[(k+1)%piece.q.size()];
                        Id ia=-1,ib=-1;for(std::size_t j=0;j<real.pointIds.size();++j)
                        {if(originalPoints.at(real.pointIds[j])==a)ia=real.pointIds[j];if(originalPoints.at(real.pointIds[j])==b)ib=real.pointIds[j];}
                        if(ia<0||ib<0)throw d::Refusal("original-diagonal-vertex");
                        bool boundary=false;
                        for(std::size_t j=0;j<real.pointIds.size();++j)
                        {
                            const auto& x=originalPoints.at(real.pointIds[j]);
                            const auto& z=originalPoints.at(real.pointIds[(j+1)%real.pointIds.size()]);
                            if(d::onSegment(a,x,z)&&d::onSegment(b,x,z)){boundary=true;break;}
                        }
                        if(!boundary)originalDiagonals[old.originalFace].insert({{std::min(ia,ib),std::max(ia,ib)}});
                    }
                }
                convexOriginal.push_back(std::move(piece));
                if(convexOriginal.size()>in.limits.facesPerPart)throw d::Refusal("original-total-partition-cap");
            }
        }
        if(!d::closed(convexOriginal))throw d::Refusal("original-partition-closure-moment");
        const auto oldMoments=d::moments(original,c,true),convexMoments=d::moments(convexOriginal,c,true);
        if(oldMoments.volume!=convexMoments.volume||oldMoments.moment!=convexMoments.moment)
            throw d::Refusal("original-partition-closure-moment");
        original.swap(convexOriginal);
        originalPredicateNormalCache.clear();
        // Independent, lazy post-swap cache: no old-vector pointers survive.
        // 0=not reached, 1=first halfspace, 2=proved same-oriented repeat.
        std::vector<unsigned char> sourceClipPlaneState(original.size(),0);
        std::set<PredicatePlane> sourceClipPlanes;
        std::vector<d::Fragment> fragments,contacts;std::set<Id> triangles;
        {
        // One immutable post-swap original vector, during source contact checks.
        // First use performs the literal old area operation after its face tick.
        // Cached values carry no source/material/point-star/geometric authority.
        std::unordered_map<const d::QFace*,d::ExactPoint> originalContactAreas;
        const auto originalContactArea=[&](const d::QFace& face)->d::ExactPoint
        {
            const auto found=originalContactAreas.find(&face);
            if(found!=originalContactAreas.end())return found->second;
            const auto normal=d::area(face.q);
            if(originalContactAreas.size()<in.limits.facesPerPart)
                originalContactAreas.emplace(&face,normal);
            return normal; // Full cache falls back to literal old exact computation.
        };
        for(const auto& t:in.window.facets)
        {
            c.tick();
            if(t.surface!=in.window.surface||t.shell!=in.window.shell||t.triangle<0
              ||!triangles.insert(t.triangle).second||t.geometryEpoch!=in.window.geometryEpoch
              ||t.membershipEpoch!=in.window.membershipEpoch||t.localRegion<0||t.globalRegion<0||!d::finite(t.normal))
                throw d::Refusal("native-facet");
            d::Ring q;
            for(unsigned j=0;j<3;++j)
            {
                if(t.pointIds[j]<0||!d::finite(t.p[j]))throw d::Refusal("source-point");
                const auto p=d::exact(t.p[j]);const auto it=sourcePoints.find(t.pointIds[j]);
                if(it!=sourcePoints.end()&&it->second!=p)throw d::Refusal("source-point-id");
                sourcePoints[t.pointIds[j]]=p;sourceBytes[t.pointIds[j]]=t.p[j];q.push_back(p);
            }
            for(std::size_t i=0;i<original.size();++i)
            {
                const auto& f=original[i];
                auto& state=sourceClipPlaneState[i];
                if(!state)state=firstPredicatePlane(f,sourceClipPlanes)?1:2;
                if(state==1)q=d::clipRing(q,originalPredicateNormal(f),f.q[0],c);
                if(q.empty())break;
            }
            if(q.size()<3||!d::nonzero(d::area(q)))continue; // exact zero measure, retained native window remains unchanged
            if(d::dot(d::area(q),d::exact(t.normal))<=0)
                throw d::Refusal("native-normal-winding");
            d::Fragment fragment;fragment.native=&t;fragment.face.q=std::move(q);
            fragment.face.role=Role::source;fragment.face.sourceTriangle=t.triangle;fragment.face.region=t.globalRegion;
            auto pieces=d::coplanarContactsWithAreas(fragment,original,c,originalContactArea);
            for(auto& piece:pieces)contacts.push_back(std::move(piece));
            if(contacts.size()>in.limits.facesPerPart)throw d::Refusal("contact-cap");
            fragments.push_back(std::move(fragment));
        }
        } // Retire cache before later original orientation changes.
        if(fragments.empty())throw d::Refusal("no-source-fragments");
        // Retire ambiguous cross-ID coincidences; never weld an imported source.
        std::map<d::ExactPoint,Id> sourceCoordinateOwner;
        for(const auto& p:sourcePoints)
            if(!sourceCoordinateOwner.emplace(p.second,p.first).second)throw d::Refusal("cross-source-point-identity");
        // A complete clipped prepared patch has paired native edges internally;
        // every one-sided edge must lie on an actual original-cell boundary.
        struct Use {std::size_t fragment;d::ExactPoint a,b;};
        std::map<std::pair<d::ExactPoint,d::ExactPoint>,std::vector<Use>> sourceEdges;
        for(std::size_t k=0;k<fragments.size();++k)
        {
            const auto& q=fragments[k].face.q;
            for(std::size_t j=0;j<q.size();++j)
            {const auto a=q[j],b=q[(j+1)%q.size()];sourceEdges[std::minmax(a,b)].push_back({k,a,b});}
        }
        for(const auto& entry:sourceEdges)
        {
            c.tick();const auto& u=entry.second;
            if(u.size()==2)
            {
                if(u[0].a!=u[1].b||u[0].b!=u[1].a)throw d::Refusal("source-winding");
                std::array<Id,2> e0,e1;
                if(!d::commonNativeEdge(*fragments[u[0].fragment].native,u[0].a,u[0].b,e0)
                  ||!d::commonNativeEdge(*fragments[u[1].fragment].native,u[1].a,u[1].b,e1)||e0!=e1)
                    throw d::Refusal("source-edge-identity");
            }
            else if(u.size()==1)
            {
                std::vector<Id> boundaryFaces;
                if(!d::boundarySegment(u[0].a,u[0].b,original,boundaryFaces,originalPredicateNormal))throw d::Refusal("source-coverage-hole");
            }
            else throw d::Refusal("source-nonmanifold");
        }
        // Prefer the original solid-core construction. If that exact
        // intersection is empty, a fresh KNOWN fluid core can drive the dual
        // radial construction. NativeFacet/window winding and metadata stay
        // immutable; only the working polyhedron orientations follow the
        // selected core material.
        bool coreIsSolid=true;
        d::Polyhedron core=original;
        for(const auto& fragment:fragments)
        {
            if(fragment.contactFace!=-1&&!fragment.solidOnCurrentCellSide)continue;
            const auto& f=fragment.face;core=d::cut(core,d::area(f.q),f.q[0],f,c);
            if(core.empty())break;
        }
        if(core.empty())
        {
            if(!in.materialClassification)throw d::Refusal("no-solid-kernel-core");
            coreIsSolid=false;core=original;
            for(const auto& fragment:fragments)
            {
                // A boundary contact is not an interior fluid halfspace.
                if(fragment.contactFace!=-1)continue;
                const auto& f=fragment.face;core=d::cut(core,d::mul(d::area(f.q),Rational(-1)),f.q[0],f,c);
                if(core.empty())throw d::Refusal("no-material-kernel-core");
            }
            for(auto& fragment:fragments)
            {
                std::reverse(fragment.face.q.begin(),fragment.face.q.end());
                if(fragment.contactFace!=-1)fragment.solidOnCurrentCellSide=!fragment.solidOnCurrentCellSide;
            }
            for(auto& contact:contacts)
            {
                std::reverse(contact.face.q.begin(),contact.face.q.end());
                contact.solidOnCurrentCellSide=!contact.solidOnCurrentCellSide;
            }
        }
        if(!d::closed(core))throw d::Refusal("core-closure");
        const auto cm=d::moments(core,c,true);if(cm.volume<=0)throw d::Refusal("core-volume");
        Point solidKernel=d::rounded(d::mul(cm.moment,Rational(1)/cm.volume));
        d::Polyhedron boundaryConstraints=original;
        for(const auto& f:fragments)if(f.contactFace==-1||f.solidOnCurrentCellSide)boundaryConstraints.push_back(f.face);
        double coreMargin=0;if(!d::kernel(boundaryConstraints,solidKernel,coreMargin))throw d::Refusal("material-kernel-margin");
        if(coreIsSolid)
        {
            if(!in.solidMembership||!in.solidMembership(solidKernel))throw d::Refusal("actual-solid-membership");
        }
        else
        {
            bool isSolid=true;
            if(d::classifiedMaterial(solidKernel,isSolid,c))throw d::Refusal("actual-fluid-membership");
        }
        c.tick();
        std::vector<d::Polyhedron> sectors;std::vector<std::vector<Id>> sectorSources;
        struct CurtainUse {std::size_t sector,face;};
        using CurtainTable=std::map<d::Ring,std::vector<CurtainUse>>;
        CurtainTable curtains;
        // The fragment vector and post-swap original boundary are immutable.
        // First geometry/binding use stays at its original radial call site;
        // later construction points reuse only apex-independent exact values.
        std::map<std::size_t,d::RadialFragmentMemo> radialFragmentMemos;
        // Each construction point owns a fresh radial geometry and curtain table.
        // Source/original faces and the shared Context remain immutable.
        auto buildRadialRaw=[&](const Point& constructionPoint,
            std::vector<d::Polyhedron>& rawSectors,std::vector<std::vector<Id>>& rawSources,
            CurtainTable& rawCurtains)
        {
            for(std::size_t fragmentIndex=0;fragmentIndex<fragments.size();++fragmentIndex)
            {
                const auto& fragment=fragments[fragmentIndex];
                c.tick();if(fragment.contactFace!=-1)continue;
                const auto& q=fragment.face.q;auto& memo=radialFragmentMemos[fragmentIndex];
                d::radialFragmentGeometry(fragment,memo);
                const auto& n=memo.normal;const auto& centre=memo.centre;
                d::Polyhedron sector=original;
                for(std::size_t j=0;j<q.size();++j)
                {
                    const auto a=q[j],b=q[(j+1)%q.size()];
                    auto sideNormal=d::cross(d::sub(b,a),d::sub(d::exact(constructionPoint),a));
                    if(d::side(sideNormal,a,centre)>0)sideNormal=d::mul(sideNormal,Rational(-1));
                    if(!d::nonzero(sideNormal))throw d::Refusal("zero-curtain");
                    for(const auto& p:q)if(d::side(sideNormal,a,p)>0)throw d::Refusal("nonconvex-source-piece");
                    d::QFace curtain;curtain.role=Role::curtain;curtain.sourceTriangle=fragment.native->triangle;
                    const auto& binding=d::radialFragmentEdge(fragment,j,original,originalPredicateNormal,memo);
                    curtain.sourceEdge=binding.sourceEdge;curtain.sourceCellFace=binding.sourceCellFace;
                    curtain.sourceCellFaces=binding.sourceCellFaces;
                    sector=d::cut(sector,sideNormal,a,curtain,c);if(sector.empty())break;
                }
                d::QFace physical=fragment.face;
                sector=d::cut(sector,d::mul(n,Rational(-1)),q[0],physical,c);
                if(sector.empty()||!d::closed(sector))throw d::Refusal("sector-closure");
                std::size_t sourceCount=0;
                for(const auto& f:sector)if(f.role==Role::source)
                {
                    ++sourceCount;
                    if(f.sourceTriangle!=fragment.native->triangle||d::area(f.q)!=d::mul(n,Rational(-1)))
                        throw d::Refusal("physical-source-piece-changed");
                }
                if(sourceCount!=1)throw d::Refusal("physical-source-piece-count");
                rawSectors.push_back(std::move(sector));rawSources.push_back({fragment.native->triangle});
                if(rawSectors.size()>in.limits.parts)throw d::Refusal("part-cap");
            }
            if(rawSectors.empty())throw d::Refusal("no-fluid-parts");
            for(std::size_t s=0;s<rawSectors.size();++s)for(std::size_t f=0;f<rawSectors[s].size();++f)
                if(rawSectors[s][f].role==Role::curtain)rawCurtains[d::canonical(rawSectors[s][f].q)].push_back({s,f});
            for(const auto& entry:rawCurtains)
            {
                if(entry.second.size()!=2)throw d::Refusal("unpaired-curtain");
                const auto& a=rawSectors[entry.second[0].sector][entry.second[0].face];
                const auto& b=rawSectors[entry.second[1].sector][entry.second[1].face];
                if(a.sourceEdge[0]<0||a.sourceEdge!=b.sourceEdge||d::add(d::area(a.q),d::area(b.q))!=d::ExactPoint{{Rational(0),Rational(0),Rational(0)}})
                    throw d::Refusal("curtain-native-edge-orientation");
            }
        };
        std::vector<d::Polyhedron> previewSectors;std::vector<std::vector<Id>> previewSources;
        std::vector<d::LowKernelSectorState> lowKernelStates;
        d::LowKernelUnionStats lowKernelStats;
        auto previewFluidKernel=[&](const std::vector<d::Polyhedron>& rawSectors,
            const std::vector<std::vector<Id>>& rawSources,const CurtainTable& rawCurtains,
            std::vector<d::Polyhedron>& finalSectors,std::vector<std::vector<Id>>& finalSources,
            std::vector<d::LowKernelSectorState>& states,d::LowKernelUnionStats& stats)->bool
        {
            std::vector<d::Polyhedron> working;std::vector<std::vector<Id>> workingSources;
            working.reserve(rawSectors.size());workingSources.reserve(rawSources.size());
            for(std::size_t s=0;s<rawSectors.size();++s)
            {
                c.tick();d::Polyhedron faces;faces.reserve(rawSectors[s].size());
                for(const auto& face:rawSectors[s]){c.tick();faces.push_back(face);}
                working.push_back(std::move(faces));workingSources.push_back(rawSources[s]);
            }
            std::vector<d::LowKernelSectorState> checked;d::LowKernelUnionStats checkedStats;
            try {d::repairLowKernelFluidSectors(working,workingSources,rawCurtains,c,checked,checkedStats);}
            catch(const d::Refusal& e)
            {
                // Only a geometric failure of the original true-centroid repair
                // admits another construction point. Budget/source failures propagate.
                if(std::strcmp(e.what(),"output-kernel")!=0)throw;
                return false;
            }
            c.tick();finalSectors.swap(working);finalSources.swap(workingSources);
            states.swap(checked);stats=checkedStats;return true;
        };
        buildRadialRaw(solidKernel,sectors,sectorSources,curtains);
        if(coreIsSolid&&!in.mergeAdjacentSectors)
        {
            bool selected=previewFluidKernel(sectors,sectorSources,curtains,
                previewSectors,previewSources,lowKernelStates,lowKernelStats);
            if(!selected)
            {
                // Search points only: the original 3um kernel/material gates
                // below decide admission. Use the actual original geometry,
                // never the query box or a case/cell/source selector.
                auto minimum=originalPoints.begin()->second,maximum=minimum;
                for(const auto& point:originalPoints)
                {
                    c.tick();for(unsigned a=0;a<3;++a)
                    {minimum[a]=std::min(minimum[a],point.second[a]);maximum[a]=std::max(maximum[a],point.second[a]);}
                }
                const Rational inset=d::number(3e-6)*Rational(65,64);
                for(unsigned a=0;a<3;++a){minimum[a]+=inset;maximum[a]-=inset;}
                std::set<Point> attempted;attempted.insert(solidKernel);
                for(unsigned corner=0;corner<8&&!selected;++corner)
                {
                    c.tick();d::ExactPoint exactCandidate;bool boxValid=true;
                    for(unsigned a=0;a<3;++a)
                    {boxValid&=minimum[a]<maximum[a];exactCandidate[a]=(corner&(1u<<a))?maximum[a]:minimum[a];}
                    if(!boxValid)continue;
                    const Point candidate=d::rounded(exactCandidate);
                    if(!d::finite(candidate)||!attempted.insert(candidate).second)continue;
                    double candidateMargin=0;
                    if(!d::kernel(boundaryConstraints,candidate,candidateMargin))continue;
                    c.tick();if(!in.leaseHeld())throw d::Refusal("lease-changed");
                    if(!in.solidMembership||!in.solidMembership(candidate))throw d::Refusal("actual-solid-membership");
                    c.tick();if(!in.leaseHeld())throw d::Refusal("lease-changed");
                    std::vector<d::Polyhedron> trialRaw,trialFinal;
                    std::vector<std::vector<Id>> trialSources,trialFinalSources;CurtainTable trialCurtains;
                    std::vector<d::LowKernelSectorState> trialStates;d::LowKernelUnionStats trialStats;
                    buildRadialRaw(candidate,trialRaw,trialSources,trialCurtains);
                    if(!previewFluidKernel(trialRaw,trialSources,trialCurtains,
                        trialFinal,trialFinalSources,trialStates,trialStats))continue;
                    c.tick();sectors.swap(trialRaw);sectorSources.swap(trialSources);curtains.swap(trialCurtains);
                    previewSectors.swap(trialFinal);previewSources.swap(trialFinalSources);
                    lowKernelStates.swap(trialStates);lowKernelStats=trialStats;
                    solidKernel=candidate;coreMargin=candidateMargin;selected=true;
                }
            }
            if(!selected)throw d::Refusal("output-kernel");
        }
        std::vector<Id> contactCorrections;
        const d::Polyhedron solid=d::fullCoverage(original,fragments,contacts,sectors,originalPoints,!coreIsSolid,contactCorrections,c);
        // Capture complete typed construction incidence BEFORE optional union
        // removes artificial curtains. This preserves original-face refinement
        // node provenance without a quadratic scan or coordinate-only authority.
        std::map<d::ExactPoint,std::set<Event>> constructionEvents;
        std::map<Id,const NativeFacet*> nativeById;
        for(const auto& t:in.window.facets)nativeById.emplace(t.triangle,&t);
        // Original rings are immutable here, after convexOriginal has been
        // swapped in. A face pointer and edge index bind only this local cache.
        struct OriginalEdgePlane
        {
            d::ExactPoint normal;
            bool ready=false,overflow=false;
        };
        std::map<const d::QFace*,std::map<std::size_t,OriginalEdgePlane>> originalEdgePlanes;
        struct OriginalPlaneSlot {std::size_t index=0;bool ready=false,overflow=false;};
        std::map<const d::QFace*,OriginalPlaneSlot> originalPlaneSlots;
        std::map<PredicatePlane,std::size_t> originalPlaneIndices;
        auto insideOriginalFace=[&](const d::ExactPoint& p,const d::QFace& f,
            std::vector<signed char>& planeOn)->bool
        {
            const auto& n=originalPredicateNormal(f);auto& slot=originalPlaneSlots[&f];
            if(!slot.ready&&!slot.overflow)
            {
                try
                {
                    const PredicatePlane key{n,d::dot(n,f.q.front())};
                    slot.index=originalPlaneIndices.emplace(key,originalPlaneIndices.size()).first->second;
                    slot.ready=true;
                }
                catch(const std::overflow_error&){slot.overflow=true;}
            }
            if(slot.ready)
            {
                if(planeOn.size()<=slot.index)planeOn.resize(slot.index+1,-1);
                auto& on=planeOn[slot.index];
                // First use remains the literal side formula at this old site.
                // Only the same exact directed plane shares this arithmetic bit.
                if(on<0)on=d::side(n,f.q.front(),p)==0?1:0;
                if(!on)return false;
            }
            else if(d::side(n,f.q.front(),p)!=0)return false;
            auto& planes=originalEdgePlanes[&f];
            for(std::size_t i=0;i<f.q.size();++i)
            {
                auto& cached=planes[i];
                if(!cached.ready&&!cached.overflow)
                {
                    try
                    {
                        cached.normal=d::cross(n,d::sub(f.q[(i+1)%f.q.size()],f.q[i]));
                        cached.ready=true;
                    }
                    catch(const std::overflow_error&){cached.overflow=true;}
                }
                if(cached.ready)
                {
                    try
                    {
                        if(d::side(cached.normal,f.q[i],p)<0)return false;
                        continue;
                    }
                    catch(const std::overflow_error&){}
                }
                // Only new arithmetic overflow falls back; the legacy
                // expression and any exception it raises remain unchanged.
                if(d::dot(d::cross(d::sub(f.q[(i+1)%f.q.size()],f.q[i]),
                    d::sub(p,f.q[i])),n)<0)return false;
            }
            return true;
        };
        std::map<d::ExactPoint,std::vector<const d::QFace*>> containingOriginalFaces;
        auto originalIncidences=[&](const d::ExactPoint& p)->const std::vector<const d::QFace*>&
        {
            auto found=containingOriginalFaces.find(p);
            if(found!=containingOriginalFaces.end())return found->second;
            std::vector<const d::QFace*> matched;
            std::vector<signed char> planeOn(originalPlaneIndices.size(),-1);
            for(const auto& f:original)if(insideOriginalFace(p,f,planeOn))matched.push_back(&f);
            return containingOriginalFaces.emplace(p,std::move(matched)).first->second;
        };
        for(const auto& sector:sectors)for(const auto& face:sector)for(const auto& p:face.q)
        {
            c.tick();auto& events=constructionEvents[p];
            for(const auto& old:originalPoints)if(old.second==p)
            {Event e;e.kind=Event::Kind::originalPoint;e.originalPoint=old.first;events.insert(e);}
            const auto oldSource=sourceCoordinateOwner.find(p);
            if(oldSource!=sourceCoordinateOwner.end())
            {
                Event e;e.kind=Event::Kind::sourcePoint;e.surface=in.window.surface;e.shell=in.window.shell;
                e.originalPoint=oldSource->second;e.geometryEpoch=in.window.geometryEpoch;e.membershipEpoch=in.window.membershipEpoch;events.insert(e);
            }
            if(face.role==Role::source)
            {
                const auto& t=*nativeById.at(face.sourceTriangle);std::array<Id,2> edge{{-1,-1}};
                for(unsigned j=0;j<3;++j)if(d::onSegment(p,d::exact(t.p[j]),d::exact(t.p[(j+1)%3])))
                    edge={{std::min(t.pointIds[j],t.pointIds[(j+1)%3]),std::max(t.pointIds[j],t.pointIds[(j+1)%3])}};
                for(const auto* cachedFace:originalIncidences(p))
                {const auto& f=*cachedFace;
                    Event e;e.surface=t.surface;e.shell=t.shell;e.sourceTriangle=t.triangle;e.originalFace=f.originalFace;
                    e.geometryEpoch=t.geometryEpoch;e.membershipEpoch=t.membershipEpoch;
                    e.kind=Event::Kind::sourceEdgeCellFace;e.sourceEdge=edge;events.insert(e);
                    const auto& real=*originalById.at(f.originalFace);
                    for(std::size_t j=0;j<real.pointIds.size();++j)
                    {
                        Id a=real.pointIds[j],b=real.pointIds[(j+1)%real.pointIds.size()];
                        if(d::onSegment(p,originalPoints.at(a),originalPoints.at(b)))
                        {e.kind=Event::Kind::sourceTriangleCellEdge;e.originalEdge={{std::min(a,b),std::max(a,b)}};events.insert(e);}
                    }
                }
            }
            if(face.role==Role::curtain)for(const auto* cachedFace:originalIncidences(p))
            {const auto& f=*cachedFace;
                Event e;e.kind=Event::Kind::curtainCellFace;e.surface=in.window.surface;e.shell=in.window.shell;
                e.sourceTriangle=face.sourceTriangle;e.sourceEdge=face.sourceEdge;e.originalFace=f.originalFace;
                e.sourceCellFace=face.sourceCellFace;e.constructionCell=in.cell.id;
                e.geometryEpoch=in.window.geometryEpoch;e.membershipEpoch=in.window.membershipEpoch;events.insert(e);
            }
        }
        // Index actual old-face refinement lines by their typed parent face.
        // Exact line equality only retrieves an already bound construction;
        // it never creates source/point identity or physical-crossing permission.
        using Line=std::pair<d::ExactPoint,d::ExactPoint>;
        auto line=[&](const d::ExactPoint& a,const d::ExactPoint& b)->Line
        {
            auto direction=d::sub(b,a);unsigned axis=0;
            while(axis<3&&direction[axis]==0)++axis;
            if(axis==3)throw d::Refusal("zero-arrangement-edge");
            direction=d::mul(direction,Rational(1)/direction[axis]);
            return {direction,d::sub(a,d::mul(direction,a[axis]))};
        };
        struct LineBinding {Event event;d::ExactPoint normal;};
        std::map<std::pair<Id,Line>,std::vector<LineBinding>> refinementLines;
        // Only unchanged original-role faces in const sectors use this cache.
        // It is last read before sectors.swap; stack contact qold stays uncached.
        std::map<const d::QFace*,d::ExactPoint> eventOldAreas;
        auto eventOldArea=[&](const d::QFace& old)->const d::ExactPoint&
        {
            const auto found=eventOldAreas.find(&old);
            if(found!=eventOldAreas.end())return found->second;
            return eventOldAreas.emplace(&old,d::area(old.q)).first->second;
        };
        auto bind=[&](const d::QFace& old,const d::QFace& parent,
                      const d::ExactPoint& normal,const Line& edgeLine,Event::Kind kind,
                      const d::ExactPoint* oldArea=nullptr)
        {
            const auto transverse=oldArea?d::cross(*oldArea,normal):d::cross(d::area(old.q),normal);
            if(!d::nonzero(transverse))return;
            Event e;e.kind=kind;e.surface=in.window.surface;e.shell=in.window.shell;
            e.sourceTriangle=parent.sourceTriangle;e.sourceEdge=parent.sourceEdge;
            e.sourceCellFace=parent.sourceCellFace;e.constructionCell=in.cell.id;
            e.originalFace=old.originalFace;
            e.geometryEpoch=in.window.geometryEpoch;e.membershipEpoch=in.window.membershipEpoch;
            refinementLines[{old.originalFace,edgeLine}].push_back({e,normal});
        };
        // Only immutable sector parents enter this cache; the contact-loop
        // stack qold/parent objects below are deliberately not cached.
        std::map<const d::QFace*,d::ExactPoint> eventParentAreas;
        for(const auto& sector:sectors)for(const auto& old:sector)
        {
            if(old.role!=Role::original)continue;
            for(std::size_t j=0;j<old.q.size();++j)
            {
                const auto& a=old.q[j];const auto& b=old.q[(j+1)%old.q.size()];
                const auto edgeLine=line(a,b);
                for(const auto& parent:sector)
                {
                    c.tick();if(parent.role!=Role::curtain&&parent.role!=Role::source)continue;
                    auto cached=eventParentAreas.find(&parent);
                    if(cached==eventParentAreas.end())
                        cached=eventParentAreas.emplace(&parent,d::primitivePositiveNormal(d::area(parent.q))).first;
                    const auto& normal=cached->second;
                    if(d::side(normal,parent.q[0],a)!=0||d::side(normal,parent.q[0],b)!=0)continue;
                    bind(old,parent,normal,edgeLine,parent.role==Role::curtain?
                         Event::Kind::curtainCellFace:Event::Kind::sourcePlaneCellFace,&eventOldArea(old));
                }
            }
        }
        for(const auto& fragment:contacts)
        {
            const auto& old=*originalById.at(fragment.contactFace);
            d::QFace qold;qold.originalFace=old.id;
            for(const auto id:old.pointIds)qold.q.push_back(originalPoints.at(id));
            const auto normal=d::area(qold.q);const auto& native=*fragment.native;
            for(unsigned j=0;j<3;++j)
            {
                c.tick();const auto a=d::exact(native.p[j]),b=d::exact(native.p[(j+1)%3]);
                const auto edge=d::sub(b,a);if(!d::nonzero(edge))continue;
                d::QFace parent;parent.sourceTriangle=native.triangle;
                parent.sourceEdge={{std::min(native.pointIds[j],native.pointIds[(j+1)%3]),
                                    std::max(native.pointIds[j],native.pointIds[(j+1)%3])}};
                bind(qold,parent,d::cross(edge,normal),line(a,b),Event::Kind::sourceEdgeLineCellFace);
            }
        }
        for(const auto& face:solid)for(std::size_t j=0;j<face.q.size();++j)
        {
            c.tick();const auto& p=face.q[j];
            if(constructionEvents.find(p)!=constructionEvents.end()
              ||sourceCoordinateOwner.find(p)!=sourceCoordinateOwner.end())continue;
            bool old=false;for(const auto& entry:originalPoints)if(entry.second==p){old=true;break;}
            if(old)continue;
            if(face.originalFace<0)throw d::Refusal("unbound-solid-source-node");
            auto& events=constructionEvents[p];
            std::vector<d::ExactPoint> normals;
            auto addNormal=[&](const d::ExactPoint& normal)
            {
                if(!d::nonzero(normal)||normals.size()==3)return;
                if(normals.empty()||(normals.size()==1&&d::nonzero(d::cross(normals[0],normal)))||
                   (normals.size()==2&&d::dot(normals[0],d::cross(normals[1],normal))!=0))
                    normals.push_back(normal);
            };
            for(const auto* originalFace:originalIncidences(p))
            {
                const auto normal=d::area(originalFace->q);addNormal(normal);
                const auto& native=*originalById.at(originalFace->originalFace);
                for(std::size_t k=0;k<native.pointIds.size();++k)
                {
                    c.tick();Id a=native.pointIds[k],b=native.pointIds[(k+1)%native.pointIds.size()];
                    const auto& pa=originalPoints.at(a);const auto& pb=originalPoints.at(b);
                    if(!d::onSegment(p,pa,pb))continue;
                    Event e;e.kind=Event::Kind::originalEdgeRefinement;
                    e.originalFace=native.id;e.originalEdge={{std::min(a,b),std::max(a,b)}};
                    e.surface=in.window.surface;e.shell=in.window.shell;
                    e.geometryEpoch=in.window.geometryEpoch;e.membershipEpoch=in.window.membershipEpoch;
                    e.constructionCell=in.cell.id;events.insert(e);
                    addNormal(d::cross(d::sub(pb,pa),normal));
                }
            }
            for(const auto& entry:originalDiagonals)for(const auto& edge:entry.second)
            {
                const auto& a=originalPoints.at(edge[0]);const auto& b=originalPoints.at(edge[1]);
                if(!d::onSegment(p,a,b))continue;
                const auto& real=*originalById.at(entry.first);d::Ring q;
                for(const auto id:real.pointIds)q.push_back(originalPoints.at(id));
                Event e;e.kind=Event::Kind::originalFaceDiagonal;e.originalFace=entry.first;e.originalEdge=edge;
                e.surface=in.window.surface;e.shell=in.window.shell;e.geometryEpoch=in.window.geometryEpoch;
                e.membershipEpoch=in.window.membershipEpoch;e.constructionCell=in.cell.id;events.insert(e);
                addNormal(d::cross(d::sub(b,a),d::area(q)));
            }
            const std::array<Line,2> adjacent{{line(face.q[(j+face.q.size()-1)%face.q.size()],p),
                                             line(p,face.q[(j+1)%face.q.size()])}};
            for(const auto& edgeLine:adjacent)
            {
                const auto found=refinementLines.find({face.originalFace,edgeLine});
                if(found==refinementLines.end())continue;
                for(const auto& binding:found->second)
                {events.insert(binding.event);addNormal(binding.normal);}
            }
            if(events.empty()||normals.size()!=3)throw d::Refusal("unbound-solid-arrangement-node");
        }
        // Coverage and event provenance were checked on the SELECTED raw
        // construction, before any preview union removed an artificial curtain.
        // The legacy explicit true option and the dual route remain unchanged.
        if(coreIsSolid&&!in.mergeAdjacentSectors)
        {sectors.swap(previewSectors);sectorSources.swap(previewSources);}
        // Optional exact union removes only paired artificial curtains, never
        // source faces or positive original face pieces. Actual stock quality
        // still decides whether any resulting mesh can be published.
        if(in.mergeAdjacentSectors)
        {
            bool change=true;
            while(change)
            {
                c.tick();change=false;
                std::map<d::Ring,std::vector<CurtainUse>> live;
                for(std::size_t s=0;s<sectors.size();++s)if(!sectors[s].empty())
                for(std::size_t f=0;f<sectors[s].size();++f)if(sectors[s][f].role==Role::curtain)
                    live[d::canonical(sectors[s][f].q)].push_back({s,f});
                for(const auto& entry:live)
                {
                    c.tick();if(entry.second.size()!=2)throw d::Refusal("union-curtain");
                    auto u=entry.second[0],v=entry.second[1];if(u.sector==v.sector)throw d::Refusal("union-self");
                    d::Polyhedron proposed;
                    for(std::size_t f=0;f<sectors[u.sector].size();++f)if(f!=u.face)proposed.push_back(sectors[u.sector][f]);
                    for(std::size_t f=0;f<sectors[v.sector].size();++f)if(f!=v.face)proposed.push_back(sectors[v.sector][f]);
                    // If two groups share >1 curtain, reject this one-face union
                    // rather than leaving duplicated internal positive faces.
                    if(proposed.size()>in.limits.facesPerPart||!d::closed(proposed))continue;
                    const auto beforeU=d::moments(sectors[u.sector],c,true),beforeV=d::moments(sectors[v.sector],c,true),after=d::moments(proposed,c,true);
                    if(after.volume<=0||after.volume!=beforeU.volume+beforeV.volume
                      ||after.moment!=d::add(beforeU.moment,beforeV.moment))throw d::Refusal("union-moments");
                    const auto candidate=d::rounded(d::mul(after.moment,Rational(1)/after.volume));
                    double margin=0;if(!d::kernel(proposed,candidate,margin))continue;
                    sectors[u.sector]=std::move(proposed);sectors[v.sector].clear();
                    sectorSources[u.sector].insert(sectorSources[u.sector].end(),sectorSources[v.sector].begin(),sectorSources[v.sector].end());
                    sectorSources[v.sector].clear();change=true;break;
                }
            }
        }
        Output staged;staged.originalCell=in.cell.id;staged.positiveSourceFragments=fragments.size();
        staged.coreMaterial=coreIsSolid?CoreMaterial::solid:CoreMaterial::fluid;
        staged.corePoint=solidKernel;staged.coreMargin=coreMargin;
        if(coreIsSolid)staged.solidKernel=solidKernel;
        if(lowKernelStats.removedCurtainPairs>curtains.size())throw d::Refusal("low-kernel-curtain-count");
        staged.interiorSourceFragments=sectors.size();staged.internalCurtainPairs=curtains.size()-lowKernelStats.removedCurtainPairs;
        std::map<d::ExactPoint,std::size_t> nodeIds;
        std::map<std::array<Id,2>,std::pair<d::ExactPoint,d::ExactPoint>> originalEdges;
        for(const auto& f:in.cell.faces)for(std::size_t j=0;j<f.pointIds.size();++j)
        {
            Id a=f.pointIds[j],b=f.pointIds[(j+1)%f.pointIds.size()];if(a>b)std::swap(a,b);
            originalEdges[{{a,b}}]={originalPoints.at(a),originalPoints.at(b)};
        }
        auto node=[&](const d::ExactPoint& p)->std::size_t
        {
            const auto found=nodeIds.find(p);if(found!=nodeIds.end())return found->second;
            if(staged.nodes.size()>=in.limits.points)throw d::Refusal("node-cap");
            Node result;result.exactPoint=p;result.p=d::rounded(p);std::set<Event> events;
            for(const auto& old:originalPoints)if(old.second==p)
            {Event event;event.originalPoint=old.first;event.kind=Event::Kind::originalPoint;events.insert(event);}
            const auto sourceOwner=sourceCoordinateOwner.find(p);
            if(sourceOwner!=sourceCoordinateOwner.end())
            {
                Event event;event.kind=Event::Kind::sourcePoint;event.originalPoint=sourceOwner->second;
                event.surface=in.window.surface;event.shell=in.window.shell;event.geometryEpoch=in.window.geometryEpoch;
                event.membershipEpoch=in.window.membershipEpoch;events.insert(event);
                result.p=sourceBytes.at(sourceOwner->second); // preserve original source binary64 bytes
            }
            for(const auto& entry:originalDiagonals)for(const auto& edge:entry.second)
            {
                if(!d::onSegment(p,originalPoints.at(edge[0]),originalPoints.at(edge[1])))continue;
                Event e;e.kind=Event::Kind::originalFaceDiagonal;e.originalFace=entry.first;e.originalEdge=edge;
                e.surface=in.window.surface;e.shell=in.window.shell;e.geometryEpoch=in.window.geometryEpoch;
                e.membershipEpoch=in.window.membershipEpoch;e.constructionCell=in.cell.id;events.insert(e);
            }
            const auto construction=constructionEvents.find(p);
            if(construction!=constructionEvents.end())events.insert(construction->second.begin(),construction->second.end());
            // A contact-only node is bound through the real source triangle and
            // its actual coincident original face; it is never a fluid cap here.
            for(const auto& f:contacts)if(d::insideFace(p,f.face))
            {
                const auto& t=*f.native;Event e;e.kind=Event::Kind::sourceEdgeCellFace;e.surface=t.surface;e.shell=t.shell;
                e.sourceTriangle=t.triangle;e.originalFace=f.contactFace;e.geometryEpoch=t.geometryEpoch;e.membershipEpoch=t.membershipEpoch;
                d::commonNativeEdge(t,p,p,e.sourceEdge);events.insert(e);
            }
            if(events.empty())throw d::Refusal("unbound-node");
            result.events.assign(events.begin(),events.end());const auto id=staged.nodes.size();
            staged.nodes.push_back(std::move(result));nodeIds.emplace(p,id);return id;
        };
        for(const auto& f:in.cell.faces)
            staged.originalFacePlans.push_back({f.id,f.owner,f.neighbour,f.patch,f.zone,{}});
        for(std::size_t s=0;s<sectors.size();++s)if(!sectors[s].empty())
        {
            c.tick();const auto& sector=sectors[s];const auto mm=lowKernelStates.empty()?d::moments(sector,c,true):lowKernelStates[s].mm;
            if(mm.volume<=0||!d::closed(sector))throw d::Refusal("output-part");
            Part part;part.sourceTriangles=sectorSources[s];part.volume=d::rounded(mm.volume);
            part.firstMoment=d::rounded(mm.moment);part.volumeCentroid=d::rounded(d::mul(mm.moment,Rational(1)/mm.volume));
            if(!(part.volume>0))throw d::Refusal("output-volume");
            const bool hasKernel=lowKernelStates.empty()?d::kernel(sector,part.volumeCentroid,part.kernelMargin):
                (part.kernelMargin=lowKernelStates[s].margin,lowKernelStates[s].hasKernel);
            if(coreIsSolid&&!hasKernel)throw d::Refusal("output-kernel");
            if(!coreIsSolid)
            {
                if(!hasKernel)part.kernelMargin=0; // dropped SOLID, never a fluid cone
                bool isSolid=false;
                if(!d::classifiedMaterial(part.volumeCentroid,isSolid,c))throw d::Refusal("actual-solid-sector-membership");
            }
            const auto partI=coreIsSolid?staged.fluidParts.size():staged.solidParts.size();
            for(const auto& f:sector)
            {
                Face face;face.role=f.role;face.originalFace=f.originalFace;face.sourceTriangle=f.sourceTriangle;
                face.region=f.region;face.zone=f.zone;face.sourceEdge=f.sourceEdge;face.sourceCellFace=f.sourceCellFace;face.sourceCellFaces=f.sourceCellFaces;
                for(const auto& p:f.q)face.ring.push_back(node(p));
                if(f.role==Role::original)
                {
                    auto plan=std::find_if(staged.originalFacePlans.begin(),staged.originalFacePlans.end(),
                        [&](const OriginalFacePlan& p){return p.originalFace==f.originalFace;});
                    if(plan==staged.originalFacePlans.end())throw d::Refusal("original-plan");
                    if(coreIsSolid)plan->fluidPartFaces.push_back({partI,part.faces.size()});
                    else plan->solidPartFacePieces.push_back({partI,part.faces.size()});
                }
                part.faces.push_back(std::move(face));
            }
            if(coreIsSolid)staged.fluidParts.push_back(std::move(part));
            else staged.solidParts.push_back(std::move(part));
        }
        const auto sm=d::moments(solid,c,true);
        Part solidPart;solidPart.volume=d::rounded(sm.volume);
        solidPart.firstMoment=d::rounded(sm.moment);
        solidPart.volumeCentroid=d::rounded(d::mul(sm.moment,Rational(1)/sm.volume));
        double solidCentroidMargin=0;
        solidPart.kernelMargin=d::kernel(solid,solidPart.volumeCentroid,solidCentroidMargin)?solidCentroidMargin:0;
        // The explicit native-approved core candidate remains the certified
        // solid kernel; a solid volume centroid is not silently substituted.
        if(coreIsSolid)staged.solidKernelMargin=coreMargin;
        else if(!d::kernel(solid,solidPart.volumeCentroid,solidPart.kernelMargin))
            throw d::Refusal("output-fluid-kernel");
        const std::size_t remainderPart=staged.fluidParts.size();
        for(const auto& fragment:fragments)solidPart.sourceTriangles.push_back(fragment.native->triangle);
        for(const auto& f:solid)
        {
            Face face;face.role=f.role;face.originalFace=f.originalFace;
            face.sourceTriangle=f.sourceTriangle;face.region=f.region;face.zone=f.zone;
            face.sourceEdge=f.sourceEdge;face.sourceCellFace=f.sourceCellFace;face.sourceCellFaces=f.sourceCellFaces;
            for(const auto& p:f.q)face.ring.push_back(node(p));
            if(face.originalFace>=0)
                for(auto& plan:staged.originalFacePlans)if(plan.originalFace==face.originalFace)
                    if(coreIsSolid)plan.solidPartFaces.push_back(solidPart.faces.size());
                    else plan.fluidPartFaces.push_back({remainderPart,solidPart.faces.size()});
            solidPart.faces.push_back(std::move(face));
        }
        if(coreIsSolid){staged.solidPart=std::move(solidPart);staged.solidPartKnown=true;}
        else
        {
            staged.fluidParts.push_back(std::move(solidPart));
            staged.solidPartsKnown=true;
        }
        for(const auto& f:contacts)
        {
            Contact contact;contact.sourceTriangle=f.native->triangle;contact.originalFace=f.contactFace;
            contact.region=f.native->globalRegion;
            contact.solidOnCurrentCellSide=coreIsSolid?f.solidOnCurrentCellSide:!f.solidOnCurrentCellSide;
            for(const auto& p:f.face.q)contact.ring.push_back(node(p));
            if(!coreIsSolid)std::reverse(contact.ring.begin(),contact.ring.end());
            staged.contacts.push_back(std::move(contact));
        }
        for(const Id originalPoint:contactCorrections)
        {
            c.tick();const auto found=nodeIds.find(originalPoints.at(originalPoint));
            if(found==nodeIds.end())throw d::Refusal("contact-correction-unused-point");
            const auto ni=found->second;const int previousSide=in.cell.pointSides.at(originalPoint);
            if(previousSide!=1&&previousSide!=-1)throw d::Refusal("contact-correction-old-side");
            const auto& point=originalPoints.at(originalPoint);
            auto refineEdge=[&](std::vector<std::size_t>& ring)
            {
                if(std::find(ring.begin(),ring.end(),ni)!=ring.end())return true;
                for(std::size_t k=0;k<ring.size();++k)
                {
                    const auto& a=staged.nodes.at(ring[k]).exactPoint;
                    const auto& b=staged.nodes.at(ring[(k+1)%ring.size()]).exactPoint;
                    if(point==a||point==b||!d::onSegment(point,a,b))continue;
                    if(ring.size()>=in.limits.ring)throw d::Refusal("contact-edge-refinement-cap");
                    ring.insert(ring.begin()+k+1,ni);return true;
                }
                return false; // NEVER insert a source-face INTERIOR point.
            };
            const NativeFacet* witness=nullptr;
            for(std::size_t fragmentIndex=0;fragmentIndex<fragments.size();++fragmentIndex)
            {
                const auto& fragment=fragments[fragmentIndex];
                // Reuse only the literal area already proved for this immutable
                // fragment by radial construction; contacts retain the old path.
                const auto cached=radialFragmentMemos.find(fragmentIndex);
                const bool contains=cached!=radialFragmentMemos.end()&&cached->second.geometryReady?
                    d::insideFace(originalPoints.at(originalPoint),fragment.face,cached->second.normal):
                    d::insideFace(originalPoints.at(originalPoint),fragment.face);
                if(!contains)continue;
                const Id triangle=fragment.native->triangle;bool used=false;
                auto sourceUse=[&](Part& part)
                {
                    bool present=false;
                    for(auto& face:part.faces)
                        if(face.role==Role::source&&face.sourceTriangle==triangle
                          &&refineEdge(face.ring))present=true;
                    return present;
                };
                for(auto& part:staged.fluidParts)if(sourceUse(part))used=true;
                for(auto& part:staged.solidParts)if(sourceUse(part))used=true;
                if(staged.solidPartKnown&&sourceUse(staged.solidPart))used=true;
                for(auto& contact:staged.contacts)
                    if(contact.sourceTriangle==triangle&&refineEdge(contact.ring))used=true;
                if(used){witness=fragment.native;break;}
            }
            if(!witness)throw d::Refusal("contact-correction-unused-source-incidence");
            // This is a common exact EDGE subdivision by an existing native
            // original point. Refine every actual incidence in this output;
            // no coordinates, source faces, moments or material IDs change.
            for(auto& part:staged.fluidParts)for(auto& face:part.faces)refineEdge(face.ring);
            for(auto& part:staged.solidParts)for(auto& face:part.faces)refineEdge(face.ring);
            if(staged.solidPartKnown)for(auto& face:staged.solidPart.faces)refineEdge(face.ring);
            for(auto& contact:staged.contacts)refineEdge(contact.ring);
            OriginalPointContact correction;
            correction.originalPoint=originalPoint;correction.previousSide=previousSide;
            correction.sourceTriangle=witness->triangle;correction.surface=witness->surface;correction.shell=witness->shell;
            correction.geometryEpoch=witness->geometryEpoch;correction.membershipEpoch=witness->membershipEpoch;
            staged.originalPointContacts.push_back(correction);
        }
        if(staged.fluidParts.size()+staged.solidParts.size()+(staged.solidPartKnown?1:0)>2049)throw d::Refusal("total-part-cap");
        c.tick();if(!in.leaseHeld())throw d::Refusal("lease-changed");c.tick();
        staged.geometricKnown=true;out=std::move(staged);return true;
    }
    catch(const detail::Refusal& e){out=Output();if(refusal){try{*refusal=e.what();}catch(...){}}return false;}
    catch(const std::exception& e){out=Output();if(refusal){try{*refusal=std::string("exception:")+e.what();}catch(...){}}return false;}
    catch(...){out=Output();if(refusal){try{*refusal="unknown-exception";}catch(...){}}return false;}
}
struct FaceOverlayPoint
{
    Node node;
    // These are indices into the exact parent rings named by the enclosing
    // piece. The pair is a construction event, never a coordinate identity.
    std::size_t ownerEdge=0,neighbourEdge=0;
    bool ownerEdgePresent=false,neighbourEdgePresent=false;
    Id ownerVertex=-1,neighbourVertex=-1;
    std::array<Id,2> originalEdge{{-1,-1}};
};
struct SharedFacePiece
{
    Id originalFace=-1,owner=-1,neighbour=-1;
    std::size_t ownerPart=0,ownerFace=0,neighbourPart=0,neighbourFace=0;
    std::vector<FaceOverlayPoint> ring;
};
// Refine both actual incidences of ONE original internal face. The caller
// binds both complete-star outputs and applies this one staged plan once.
inline bool overlayOriginalFace
(
    const OriginalFace& original,const Output& owner,const Output& neighbour,
    std::vector<SharedFacePiece>& out,const std::function<bool()>& available,
    std::size_t maxPieces=8192,std::string* refusal=nullptr
)
{
    out.clear();if(refusal){try{refusal->clear();}catch(...){}}
    try
    {
        namespace d=detail;
        Input clock;clock.available=available;clock.limits.steps=2000000;clock.limits.ring=256;
        d::Context context{clock};context.tick();
        if(original.id<0||original.owner<0||original.neighbour<0||original.owner==original.neighbour
          ||owner.originalCell!=original.owner||neighbour.originalCell!=original.neighbour
          ||!owner.geometricKnown||!neighbour.geometricKnown||maxPieces==0||maxPieces>8192
          ||original.points.size()<3||original.points.size()!=original.pointIds.size())
            throw d::Refusal("overlay-owner");
        d::Ring boundary;for(const auto& p:original.points)boundary.push_back(d::exact(p));
        const auto normal=d::area(boundary);if(!d::nonzero(normal))throw d::Refusal("overlay-original-area");
        for(const auto& p:boundary)if(d::side(normal,boundary[0],p)!=0)throw d::Refusal("overlay-original-plane");
        const auto axis=normal[0]!=0?0:normal[1]!=0?1:2;
        struct Patch {std::size_t part,face;d::Ring ring;const Face* raw;const Output* output;bool reverse=false;};
        auto collect=[&](const Output& output,bool reverse)
        {
            std::vector<Patch> patches;const OriginalFacePlan* plan=nullptr;
            for(const auto& p:output.originalFacePlans)if(p.originalFace==original.id)
            {if(plan)throw d::Refusal("overlay-duplicate-plan");plan=&p;}
            if(!plan||plan->owner!=original.owner||plan->neighbour!=original.neighbour
              ||plan->patch!=original.patch||plan->zone!=original.zone)
                throw d::Refusal("overlay-plan-identity");
            std::set<std::pair<std::size_t,std::size_t>> unique;
            for(const auto& address:plan->fluidPartFaces)
            {
                context.tick();
                if(!unique.insert(address).second||address.first>=output.fluidParts.size()
                  ||address.second>=output.fluidParts[address.first].faces.size())
                    throw d::Refusal("overlay-plan-address");
                const Face& f=output.fluidParts[address.first].faces[address.second];
                if(f.role!=Role::original||f.originalFace!=original.id)throw d::Refusal("overlay-face-role");
                Patch p;p.part=address.first;p.face=address.second;p.raw=&f;p.output=&output;p.reverse=reverse;
                for(const auto k:f.ring)
                {
                    if(k>=output.nodes.size())throw d::Refusal("overlay-node-address");
                    const auto& n=output.nodes[k];
                    if(n.events.empty()||!d::finite(n.p)||d::side(normal,boundary[0],n.exactPoint)!=0)
                        throw d::Refusal("overlay-node-plane-or-event");
                    p.ring.push_back(n.exactPoint);
                }
                if(reverse)std::reverse(p.ring.begin(),p.ring.end());
                const auto a=d::area(p.ring);
                if(!d::nonzero(a)||d::dot(a,normal)<=0)throw d::Refusal("overlay-orientation");
                for(std::size_t j=0;j<p.ring.size();++j)
                {
                    auto n=d::cross(d::sub(p.ring[(j+1)%p.ring.size()],p.ring[j]),normal);
                    for(const auto& q:p.ring)if(d::side(n,p.ring[j],q)>0)throw d::Refusal("overlay-nonconvex");
                }
                patches.push_back(std::move(p));
            }
            return patches;
        };
        auto lhs=collect(owner,false),rhs=collect(neighbour,true);
        std::vector<Rational> coveredLeft(lhs.size()),coveredRight(rhs.size());
        std::vector<SharedFacePiece> proposed;
        auto intersection=[&](d::Ring a,const d::Ring& b)
        {
            for(std::size_t j=0;j<b.size()&&!a.empty();++j)
                a=d::clipRing(a,d::cross(d::sub(b[(j+1)%b.size()],b[j]),normal),b[j],context);
            return a;
        };
        // Independent non-overlap validation prevents area-sum cancellation
        // from hiding duplicate/missing same-side pieces.
        for(const auto* side:{&lhs,&rhs})for(std::size_t i=0;i<side->size();++i)
        for(std::size_t j=i+1;j<side->size();++j)
        {
            context.tick();const auto q=intersection((*side)[i].ring,(*side)[j].ring);
            if(q.size()>=3&&d::nonzero(d::area(q)))throw d::Refusal("overlay-duplicate-cover");
        }
        for(std::size_t i=0;i<lhs.size();++i)for(std::size_t j=0;j<rhs.size();++j)
        {
            context.tick();auto q=intersection(lhs[i].ring,rhs[j].ring);
            if(q.size()<3||!d::nonzero(d::area(q)))continue;
            const auto a=d::area(q);
            coveredLeft[i]+=a[axis];coveredRight[j]+=a[axis];
            if(proposed.size()>=maxPieces)throw d::Refusal("overlay-piece-cap");
            SharedFacePiece piece;piece.originalFace=original.id;piece.owner=original.owner;piece.neighbour=original.neighbour;
            piece.ownerPart=lhs[i].part;piece.ownerFace=lhs[i].face;piece.neighbourPart=rhs[j].part;piece.neighbourFace=rhs[j].face;
            for(const auto& p:q)
            {
                FaceOverlayPoint point;point.node.exactPoint=p;point.node.p=d::rounded(p);
                bool left=false,right=false;
                auto bind=[&](const Patch& parent,bool& found,std::size_t& edge,Id& vertex)
                {
                    for(std::size_t k=0;k<parent.ring.size();++k)
                    {
                        if(parent.ring[k]==p)
                        {
                            for(const auto index:parent.raw->ring)if(parent.output->nodes[index].exactPoint==p)
                            {
                                vertex=Id(index);const auto& node=parent.output->nodes[index];
                                point.node.events.insert(point.node.events.end(),node.events.begin(),node.events.end());
                                point.node.p=node.p;break;
                            }
                        }
                        if(d::onSegment(p,parent.ring[k],parent.ring[(k+1)%parent.ring.size()]))
                        {if(!found){edge=parent.reverse?(2*parent.ring.size()-2-k)%parent.ring.size():k;found=true;}}
                    }
                };
                bind(lhs[i],left,point.ownerEdge,point.ownerVertex);
                bind(rhs[j],right,point.neighbourEdge,point.neighbourVertex);
                point.ownerEdgePresent=left;point.neighbourEdgePresent=right;
                // An overlap vertex is an existing parent vertex or the
                // intersection of an edge from EACH actual parent plan.
                if(point.node.events.empty()&&!(left&&right))throw d::Refusal("overlay-unbound-intersection");
                for(std::size_t k=0;k<boundary.size();++k)if(d::onSegment(p,boundary[k],boundary[(k+1)%boundary.size()]))
                    point.originalEdge={{std::min(original.pointIds[k],original.pointIds[(k+1)%boundary.size()]),
                      std::max(original.pointIds[k],original.pointIds[(k+1)%boundary.size()])}};
                piece.ring.push_back(std::move(point));
            }
            proposed.push_back(std::move(piece));
        }
        for(std::size_t i=0;i<lhs.size();++i)
            if(coveredLeft[i]!=d::area(lhs[i].ring)[axis])throw d::Refusal("overlay-owner-coverage");
        for(std::size_t i=0;i<rhs.size();++i)
            if(coveredRight[i]!=d::area(rhs[i].ring)[axis])throw d::Refusal("overlay-neighbour-coverage");
        context.tick();out=std::move(proposed);return true;
    }
    catch(const detail::Refusal& e){if(refusal){try{*refusal=e.what();}catch(...){}}return false;}
    catch(const std::exception& e){if(refusal){try{*refusal=std::string("overlay-exception:")+e.what();}catch(...){}}return false;}
    catch(...){if(refusal){try{*refusal="overlay-unknown-exception";}catch(...){}}return false;}
}

} // namespace
#endif
