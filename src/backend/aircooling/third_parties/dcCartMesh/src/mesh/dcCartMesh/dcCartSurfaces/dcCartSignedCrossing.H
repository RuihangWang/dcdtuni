#ifndef dcCartSignedCrossing_H
#define dcCartSignedCrossing_H
// PRIVATE candidate. Integer generalized winding for a certified oriented cycle.
// Not a parity oracle, not provenance/coverage/mesh-quality authority.
#include "../dcCartCutCells/dcCartExactBinary64Geometry.H"
#include <array>
#include <vector>
#include <unordered_map>
#include <unordered_set>
#include <algorithm>
#include <functional>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <cmath>
#include <utility>
#include <cfenv>
#include <cfloat>
#if defined(__x86_64__) && defined(__SSE__)
#include <xmmintrin.h>
#endif
#if defined(__FAST_MATH__)
#error "signed crossing requires IEEE basic-operation interval bounds"
#endif
namespace dcCartSignedCrossing
{
using Point=std::array<double,3>;
struct Triangle
{
    std::array<std::uint64_t,3> vertices{{0,0,0}};
    std::uint64_t surface=0,originalTriangle=0;
};
struct Limits
{
    std::size_t triangles=1048576,points=2097152;
    std::size_t queryVisits=262144,queryTriangles=65536;
};
enum class Refusal {none,uncertified,capacity,clock,nonfinite,reference,degenerate,
                    orientation,ambiguous,endpoint,floatingEnvironment};
struct Stats
{
    std::size_t nodes=0,triangles=0,exactPredicates=0,rayAttempts=0;
};
struct Result
{
    bool known=false;
    std::int64_t degree=0;
    Refusal refusal=Refusal::uncertified;
};
// Conservative runtime admission for the interval filter. The current Linux
// x86_64/SSE binary64 target uses gradual underflow only when FTZ/DAZ are clear.
// Check both C fenv and SSE rounding: callers may change MXCSR directly after
// cache preparation. Unsupported targets/modes decline the accelerator.
inline bool supportedFloatingEnvironment()
{
    if(sizeof(double)!=8 || !std::numeric_limits<double>::is_iec559
       || std::numeric_limits<double>::radix!=2 || std::numeric_limits<double>::digits!=53
       || std::numeric_limits<double>::max_exponent!=1024
       || std::numeric_limits<double>::has_denorm!=std::denorm_present
       || FLT_EVAL_METHOD!=0 || std::fegetround()!=FE_TONEAREST)return false;
#if defined(__x86_64__) && defined(__SSE__)
    // MXCSR bit15=FTZ, bit6=DAZ, bits13-14=rounding control (00=nearest).
    return (_mm_getcsr()&((1u<<15)|(1u<<6)|(3u<<13)))==0;
#else
    return false;
#endif
}
namespace detail
{
// Each basic operation is rounded once into volatile binary64. Adjacent
// representable values enclose its exact real result. Overflow/NaN refuses
// the filter; exact polynomial predicates handle that case. Never uses EPS.
struct Interval {double lo=0,hi=0;bool valid=true;};
inline Interval rounded(double r)
{
    if(!std::isfinite(r))return {0,0,false};
    const double lo=std::nextafter(r,-std::numeric_limits<double>::infinity());
    const double hi=std::nextafter(r,std::numeric_limits<double>::infinity());
    return {lo,hi,std::isfinite(lo)&&std::isfinite(hi)};
}
inline Interval number(double r){return {r,r,std::isfinite(r)};}
inline Interval add(Interval a,Interval b)
{
    if(!a.valid||!b.valid)return {0,0,false};
    volatile double low=a.lo+b.lo,high=a.hi+b.hi;
    const auto l=rounded(low),h=rounded(high);
    return {l.lo,h.hi,l.valid&&h.valid};
}
inline Interval sub(Interval a,Interval b)
{
    if(!a.valid||!b.valid)return {0,0,false};
    volatile double low=a.lo-b.hi,high=a.hi-b.lo;
    const auto l=rounded(low),h=rounded(high);
    return {l.lo,h.hi,l.valid&&h.valid};
}
inline Interval mul(Interval a,Interval b)
{
    if(!a.valid||!b.valid)return {0,0,false};
    double lo=std::numeric_limits<double>::infinity(),hi=-lo;
    for(double x:{a.lo,a.hi})for(double y:{b.lo,b.hi})
    {
        volatile double product=x*y;
        const auto r=rounded(product);if(!r.valid)return {0,0,false};
        lo=std::min(lo,r.lo);hi=std::max(hi,r.hi);
    }
    return {lo,hi,true};
}
using IVector=std::array<Interval,3>;
inline IVector difference(const Point& a,const Point& b)
{
    IVector r;for(unsigned d=0;d<3;++d)r[d]=sub(number(a[d]),number(b[d]));return r;
}
inline IVector cross(const IVector& a,const IVector& b)
{
    IVector r;
    for(unsigned d=0;d<3;++d)r[d]=sub(mul(a[(d+1)%3],b[(d+2)%3]),
                                    mul(a[(d+2)%3],b[(d+1)%3]));
    return r;
}
inline Interval dot(const IVector& a,const IVector& b)
{
    Interval r=number(0);for(unsigned d=0;d<3;++d)r=add(r,mul(a[d],b[d]));return r;
}
inline int strictSign(Interval r)
{return !r.valid?0:(r.lo>0?1:(r.hi<0?-1:0));}
inline bool finite(const Point& p)
{for(double x:p)if(!std::isfinite(x))return false;return true;}
struct Budget
{
    const std::function<bool()>& available;
    bool tick()const
    // Callbacks can alter thread-local FP state; decline before a filter.
    {try{return available&&available()&&supportedFloatingEnvironment();}catch(...){return false;}}
};
inline dcCartExactBinary64Geometry::Sign planeSign
(
    const Point& a,const Point& b,const Point& c,const Point& p,
    const Budget& budget,Stats& stats
)
{
    const int filtered=strictSign(dot(cross(difference(b,a),difference(c,a)),difference(p,a)));
    if(filtered)return {true,filtered};
    ++stats.exactPredicates;
    dcCartExactBinary64Geometry::Context exact([&](){return budget.tick();},512);
    return dcCartExactBinary64Geometry::planeSide(a,b,c,p,exact);
}
// value1 proves nonzero oriented area; value0 proves exact zero; !known refuses.
inline dcCartExactBinary64Geometry::Sign areaSign(const Point& a,const Point& b,const Point& c,const Budget& budget)
{
    const auto filtered=cross(difference(b,a),difference(c,a));
    for(const auto& component:filtered)if(strictSign(component))return {true,1};
    namespace E=dcCartExactBinary64Geometry;
    E::Context exact([&](){return budget.tick();},512);
    const auto origin=E::vector(a,exact);
    const auto n=E::cross(E::difference(E::vector(b,exact),origin,exact),
                          E::difference(E::vector(c,exact),origin,exact),exact);
    if(!exact.good())return {false,0};
    for(const auto& component:n)if(E::sign(component))return {true,1};
    return {true,0};
}
// A zero-area affine simplex maps exactly to a point/segment.
// Bbox membership and exact collinearity prove ON; no distance/EPS test.
inline dcCartExactBinary64Geometry::Sign degenerateSupport
(const Point& a,const Point& b,const Point& c,const Point& q,const Budget& budget)
{
    for(unsigned d=0;d<3;++d)
        if(q[d]<std::min(a[d],std::min(b[d],c[d])) ||
           q[d]>std::max(a[d],std::max(b[d],c[d])))return {true,0};
    const Point* other=b!=a?&b:(c!=a?&c:nullptr);
    if(!other)return {true,1}; // bbox already proved equality with the point
    const auto area=areaSign(a,*other,q,budget);
    if(!area.known)return {false,0};
    return {true,area.value?0:1};
}
struct Pair
{
    std::uint64_t a,b;
    bool operator==(const Pair& p)const{return a==p.a&&b==p.b;}
};
struct PairHash
{
    std::size_t operator()(const Pair& p)const
    {return std::hash<std::uint64_t>()(p.a)^(std::hash<std::uint64_t>()(p.b)+0x9e3779b9u+(p.a<<6)+(p.a>>2));}
};
struct EdgeUse {unsigned forward=0,reverse=0;};
struct Box
{
    Point lo{{std::numeric_limits<double>::infinity(),std::numeric_limits<double>::infinity(),std::numeric_limits<double>::infinity()}};
    Point hi{{-std::numeric_limits<double>::infinity(),-std::numeric_limits<double>::infinity(),-std::numeric_limits<double>::infinity()}};
    void add(const Point& p)
    {for(unsigned d=0;d<3;++d){lo[d]=std::min(lo[d],p[d]);hi[d]=std::max(hi[d],p[d]);}}
    void add(const Box& b){add(b.lo);add(b.hi);}
    bool contains(const Point& p)const
    {for(unsigned d=0;d<3;++d)if(p[d]<lo[d]||p[d]>hi[d])return false;return true;}
    bool overlaps(const Box& b)const
    {for(unsigned d=0;d<3;++d)if(hi[d]<b.lo[d]||lo[d]>b.hi[d])return false;return true;}
};
struct Node {Box box;std::size_t begin=0,end=0,left=0,right=0;bool leaf=true;};
}
// Geometry is copied by value from final prepared vertices once. Caller must
// retire/reprepare this object if those vertices or membership change.
class PreparedShell
{
    std::vector<Point> points_;
    std::vector<Triangle> triangles_;
    std::vector<unsigned char> zeroArea_;
    std::size_t zeroAreaCount_=0;
    std::vector<detail::Box> boxes_;
    std::vector<std::size_t> order_;
    std::vector<detail::Node> nodes_;
    detail::Box bounds_;
    Limits limits_;
    bool certified_=false;
    Refusal refusal_=Refusal::uncertified;
    std::size_t node(std::size_t begin,std::size_t end,const detail::Budget& budget)
    {
        if(!budget.tick())throw Refusal::clock;
        const std::size_t index=nodes_.size();nodes_.push_back(detail::Node());
        nodes_[index].begin=begin;nodes_[index].end=end;
        for(std::size_t k=begin;k<end;++k)
        {
            if((k-begin)%256==0&&!budget.tick())throw Refusal::clock;
            nodes_[index].box.add(boxes_[order_[k]]);
        }
        if(end-begin<=8)return index;
        unsigned axis=0;
        for(unsigned d=1;d<3;++d)
            if(nodes_[index].box.hi[d]/2-nodes_[index].box.lo[d]/2>
               nodes_[index].box.hi[axis]/2-nodes_[index].box.lo[axis]/2)axis=d;
        const std::size_t middle=begin+(end-begin)/2;
        std::size_t comparisons=0;
        std::nth_element(order_.begin()+begin,order_.begin()+middle,order_.begin()+end,
            [&](std::size_t a,std::size_t b)
            {
                if(++comparisons%1024==0&&!budget.tick())throw Refusal::clock;
                const double ca=boxes_[a].lo[axis]/2+boxes_[a].hi[axis]/2;
                const double cb=boxes_[b].lo[axis]/2+boxes_[b].hi[axis]/2;
                return ca<cb||(ca==cb&&a<b);
            });
        const std::size_t left=node(begin,middle,budget),right=node(middle,end,budget);
        nodes_[index].left=left;nodes_[index].right=right;nodes_[index].leaf=false;
        return index;
    }
    // Every original triangle stays in the BVH. Before any known degree,
    // inspect point-containing boxes for exact degenerate support membership.
    Result checkDegenerateSupport(const Point& q,const detail::Budget& budget,Stats& stats)const
    {
        if(!zeroAreaCount_)return {true,0,Refusal::none};
        std::array<std::size_t,64> stack;std::size_t top=1;stack[0]=0;
        while(top)
        {
            if(!budget.tick())return {false,0,Refusal::clock};
            if(stats.nodes>=limits_.queryVisits)return {false,0,Refusal::capacity};
            ++stats.nodes;const auto& n=nodes_[stack[--top]];
            if(!n.box.contains(q))continue;
            if(!n.leaf)
            {
                if(top+2>stack.size())return {false,0,Refusal::capacity};
                stack[top++]=n.right;stack[top++]=n.left;continue;
            }
            for(std::size_t k=n.begin;k<n.end;++k)
            {
                const std::size_t ti=order_[k];
                if(!zeroArea_[ti]||!boxes_[ti].contains(q))continue;
                if(stats.triangles>=limits_.queryTriangles)return {false,0,Refusal::capacity};
                ++stats.triangles;++stats.exactPredicates;
                const auto& t=triangles_[ti];
                const auto support=detail::degenerateSupport(points_[t.vertices[0]],
                    points_[t.vertices[1]],points_[t.vertices[2]],q,budget);
                if(!support.known||support.value)return {false,0,Refusal::ambiguous};
            }
        }
        return {true,0,Refusal::none};
    }
    Result ray(const Point& q,const Point& far,const detail::Budget& budget,Stats& stats)const
    {
        if(bounds_.contains(far))return {false,0,Refusal::endpoint};
        detail::Box rayBox;rayBox.add(q);rayBox.add(far);
        std::array<std::size_t,64> stack;std::size_t top=1;stack[0]=0;
        std::int64_t degree=0;
        while(top)
        {
            if(!budget.tick())return {false,0,Refusal::clock};
            if(stats.nodes>=limits_.queryVisits)return {false,0,Refusal::capacity};
            ++stats.nodes;
            const auto& n=nodes_[stack[--top]];
            if(!n.box.overlaps(rayBox))continue;
            if(!n.leaf)
            {
                if(top+2>stack.size())return {false,0,Refusal::capacity};
                stack[top++]=n.right;stack[top++]=n.left;continue;
            }
            for(std::size_t k=n.begin;k<n.end;++k)
            {
                const std::size_t ti=order_[k];if(!boxes_[ti].overlaps(rayBox))continue;
                if(stats.triangles>=limits_.queryTriangles)return {false,0,Refusal::capacity};
                ++stats.triangles;
                // The exact support gate already admitted this query.
                // Off that support this proven zero-area contribution is zero.
                if(zeroArea_[ti])continue;
                const auto& t=triangles_[ti];
                const auto& a=points_[t.vertices[0]];
                const auto& b=points_[t.vertices[1]];
                const auto& c=points_[t.vertices[2]];
                const auto sq=detail::planeSign(a,b,c,q,budget,stats);
                const auto sf=detail::planeSign(a,b,c,far,budget,stats);
                if(!sq.known||!sf.known)return {false,0,Refusal::ambiguous};
                if(sq.value&&sf.value&&sq.value==sf.value)continue;
                const auto e0=detail::planeSign(q,far,a,b,budget,stats);
                const auto e1=detail::planeSign(q,far,b,c,budget,stats);
                const auto e2=detail::planeSign(q,far,c,a,budget,stats);
                if(!e0.known||!e1.known||!e2.known)return {false,0,Refusal::ambiguous};
                const bool positive=e0.value>0||e1.value>0||e2.value>0;
                const bool negative=e0.value<0||e1.value<0||e2.value<0;
                if(positive&&negative)continue; // infinite-line intersection outside triangle
                if(!sq.value||!sf.value||!e0.value||!e1.value||!e2.value)
                    return {false,0,Refusal::ambiguous}; // endpoint/edge/vertex/coplanar
                degree+=(sf.value>0?1:-1); // signed crossing, never modulo two
            }
        }
        return {true,degree,Refusal::none};
    }
public:
    bool certified()const{return certified_;}
    Refusal refusal()const{return refusal_;}
    std::size_t zeroAreaTriangles()const{return zeroAreaCount_;}
    bool prepare(const std::vector<Point>& points,const std::vector<Triangle>& triangles,
                 const std::function<bool()>& available,Limits limits=Limits())
    {
        *this=PreparedShell();limits_=limits;
        if(!supportedFloatingEnvironment()){refusal_=Refusal::floatingEnvironment;return false;}
        if(points.empty()||triangles.empty()||points.size()>limits.points||
           triangles.size()>limits.triangles||limits.points>2097152||limits.triangles>1048576||
           limits.queryVisits>262144||limits.queryTriangles>65536)
        {refusal_=Refusal::capacity;return false;}
        const detail::Budget budget{available};
        try
        {
            if(!budget.tick()){refusal_=Refusal::clock;return false;}
            for(std::size_t i=0;i<points.size();++i)
            {
                if(i%256==0&&!budget.tick()){refusal_=Refusal::clock;return false;}
                if(!detail::finite(points[i])){refusal_=Refusal::nonfinite;return false;}
            }
            std::unordered_map<detail::Pair,detail::EdgeUse,detail::PairHash> edges;
            std::unordered_set<detail::Pair,detail::PairHash> identities;
            edges.reserve(3*triangles.size());identities.reserve(triangles.size());
            boxes_.reserve(triangles.size());zeroArea_.reserve(triangles.size());
            for(const auto& t:triangles)
            {
                if(!budget.tick()){refusal_=Refusal::clock;return false;}
                if(!identities.insert({t.surface,t.originalTriangle}).second)
                {refusal_=Refusal::reference;return false;}
                for(auto v:t.vertices)if(v>=points.size())
                {refusal_=Refusal::reference;return false;}
                const auto area=detail::areaSign(points[t.vertices[0]],points[t.vertices[1]],points[t.vertices[2]],budget);
                if(!area.known){refusal_=Refusal::ambiguous;return false;}
                zeroArea_.push_back(area.value==0);zeroAreaCount_+=area.value==0;
                detail::Box bb;for(auto v:t.vertices)bb.add(points[v]);boxes_.push_back(bb);bounds_.add(bb);
                for(unsigned k=0;k<3;++k)
                {
                    const auto a=t.vertices[k],b=t.vertices[(k+1)%3];
                    if(a==b)continue; // [v,v] is the zero oriented1-chain
                    auto& use=edges[{std::min(a,b),std::max(a,b)}];
                    if(a<b)++use.forward;else ++use.reverse;
                    if(use.forward>1||use.reverse>1)
                    {refusal_=Refusal::orientation;return false;}
                }
            }
            std::size_t edgeCount=0;
            for(const auto& edge:edges)
            {
                if(++edgeCount%256==0&&!budget.tick()){refusal_=Refusal::clock;return false;}
                if(edge.second.forward!=1||edge.second.reverse!=1)
                {refusal_=Refusal::orientation;return false;}
            }
            points_=points;triangles_=triangles;order_.resize(triangles.size());
            for(std::size_t k=0;k<order_.size();++k)order_[k]=k;
            nodes_.reserve(2*triangles.size());node(0,triangles.size(),budget);
            certified_=true;refusal_=Refusal::none;return true;
        }
        catch(Refusal r){refusal_=r;return false;}
        catch(...){refusal_=Refusal::capacity;return false;}
    }
    // Diagnostic single ray. A known result still requires a certified cycle
    // and an outside endpoint; ambiguity never becomes a guessed zero.
    Result classifyRay(const Point& q,const Point& far,const std::function<bool()>& available,
                       Stats* out=nullptr)const
    {
        if(out)*out=Stats();
        if(!supportedFloatingEnvironment())return {false,0,Refusal::floatingEnvironment};
        Stats stats;const detail::Budget budget{available};
        Result result{false,0,refusal_};
        if(!certified_)return result;
        if(!detail::finite(q)||!detail::finite(far))return {false,0,Refusal::nonfinite};
        if(!budget.tick())return {false,0,Refusal::clock};
        const auto support=checkDegenerateSupport(q,budget,stats);
        if(!support.known){if(out)*out=stats;return support;}
        ++stats.rayAttempts;result=ray(q,far,budget,stats);
        if(out)*out=stats;
        return result;
    }
    Result classify(const Point& q,const std::function<bool()>& available,Stats* out=nullptr)const
    {
        if(out)*out=Stats();
        if(!supportedFloatingEnvironment())return {false,0,Refusal::floatingEnvironment};
        Stats stats;const detail::Budget budget{available};
        if(!certified_)return {false,0,refusal_};
        if(!detail::finite(q))return {false,0,Refusal::nonfinite};
        if(!budget.tick())return {false,0,Refusal::clock};
        const auto support=checkDegenerateSupport(q,budget,stats);
        if(!support.known){if(out)*out=stats;return support;}
        if(!bounds_.contains(q)){if(out)*out=stats;return {true,0,Refusal::none};}
        Result result{false,0,Refusal::ambiguous};
        for(unsigned attempt=0;attempt<6;++attempt)
        {
            const unsigned axis=attempt%3;Point far=q;
            const double halfSpan=bounds_.hi[axis]/2-bounds_.lo[axis]/2;
            const double step=std::max(1.0,halfSpan);
            far[axis]=bounds_.hi[axis]+step;
            if(attempt>=3)for(unsigned d=0;d<3;++d)if(d!=axis)
            {
                // Slender deterministic ray box. Only the endpoint changes;
                // the classified physical point and every EPS stay unchanged.
                const double half=bounds_.hi[d]/2-bounds_.lo[d]/2;
                far[d]=q[d]+std::max(1.0,half)*double(d+1)/128.0;
            }
            if(!detail::finite(far)||!(far[axis]>bounds_.hi[axis]))continue;
            ++stats.rayAttempts;result=ray(q,far,budget,stats);
            if(result.known||result.refusal==Refusal::clock||result.refusal==Refusal::capacity)break;
        }
        if(out)*out=stats;
        return result;
    }
};
}
#endif
