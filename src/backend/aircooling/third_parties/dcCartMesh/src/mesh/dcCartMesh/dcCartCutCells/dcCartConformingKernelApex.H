#ifndef dcCartConformingKernelApex_H
#define dcCartConformingKernelApex_H
#include "dcCartCompleteCavityTwoLobe.H"
#include <cfenv>
#include <limits>
#if defined(__x86_64__) || defined(__i386__)
#include <xmmintrin.h>
#endif
namespace Foam { namespace dcCartConformingKernelApex
{
struct Plane { vector area; point centre; };
struct Result
{
    point candidate=point::zero;
    scalar minimumMargin=0;
    std::size_t combinations=0,marginQueries=0;
    bool originalSatisfied=false,searchExhausted=false;
};
inline bool fp()
{
#ifdef __FAST_MATH__
    return false;
#else
    if(sizeof(scalar)!=8||!std::numeric_limits<scalar>::is_iec559
       ||std::numeric_limits<scalar>::digits!=53||std::fegetround()!=FE_TONEAREST)return false;
#if defined(__x86_64__) || defined(__i386__)
    if(_mm_getcsr()&((1u<<15)|(1u<<6)))return false;
#else
    return false;
#endif
    return true;
#endif
}
// Only candidate generation. Every accepted point passes the exact expression
// used by the existing cone preflight, with its unchanged strict threshold.
inline bool propose
(
    const std::vector<Plane>& faces,const point& original,scalar parentVolume,
    const std::function<bool()>& workAvailable,Result& result
)
{
    Result out;out.candidate=original;
    auto awake=[&]()
    {return fp()&&workAvailable&&workAvailable()&&fp();};
    auto finitePoint=[](const vector& p)
    {return std::isfinite(p.x())&&std::isfinite(p.y())&&std::isfinite(p.z());};
    if(!awake()||!finitePoint(original)||!std::isfinite(parentVolume)
       ||parentVolume<=VSMALL||faces.size()<4||faces.size()>64)return false;
    std::vector<std::array<scalar,4>> planes;planes.reserve(faces.size());
    for(const auto& f:faces)
    {
        if(!awake()||!finitePoint(f.area)||!finitePoint(f.centre))return false;
        const scalar length=mag(f.area);
        if(!std::isfinite(length)||length<=VSMALL)return false;
        const vector n=f.area/length;
        // Local-frame candidate arithmetic; original stored-area/centre
        // expression below remains the only final halfspace acceptance.
        const scalar localOffset=n&(f.centre-original);
        if(!std::isfinite(localOffset))return false;
        planes.push_back({{n.x(),n.y(),n.z(),localOffset}});
    }
    auto verify=[&](const point& p,scalar& smallest)
    {
        if(!awake()||!finitePoint(p))return false;
        scalar minimum=GREAT;
        for(const auto& f:faces)
        {
            if(!awake())return false;
            ++out.marginQueries;
            const scalar margin=(f.area&(f.centre-p))/max(mag(f.area),VSMALL);
            if(!std::isfinite(margin)||margin<=3e-6)return false;
            minimum=min(minimum,margin);
        }
        if(!awake())return false;smallest=minimum;return true;
    };
    scalar originalMargin=0;
    if(verify(original,originalMargin))
    {
        out.originalSatisfied=true;out.minimumMargin=originalMargin;
        if(!awake())return false;result=out;return true;
    }
    if(!awake())return false;
    bool found=false;point best=point::zero;scalar bestMargin=0;
    const std::size_t n=planes.size();bool capped=false;
    for(std::size_t a=0;a+3<n&&!capped;++a)
      for(std::size_t b=a+1;b+2<n&&!capped;++b)
        for(std::size_t c=b+1;c+1<n&&!capped;++c)
          for(std::size_t d=c+1;d<n;++d)
    {
        if(!awake())return false;
        if(out.combinations>=65536){capped=true;break;}
        ++out.combinations;
        const std::array<std::size_t,4> indices{{a,b,c,d}};
        std::array<std::array<scalar,5>,4> system;
        for(int row=0;row<4;++row)
        {
            const auto& p=planes[indices[row]];
            system[row]={{p[0],p[1],p[2],1,p[3]}};
        }
        std::array<scalar,4> solved;
        if(!dcCartCompleteCavityTwoLobe::solve4(system,solved)
           ||!awake()||solved[3]<=3e-6)continue;
        const point candidate=original+vector(solved[0],solved[1],solved[2]);
        scalar margin=0;
        if(!verify(candidate,margin))
        {if(!awake())return false;continue;}
        if(!found||margin>bestMargin
           ||(margin==bestMargin&&std::make_tuple(candidate.x(),candidate.y(),candidate.z())
                                  <std::make_tuple(best.x(),best.y(),best.z())))
        {found=true;best=candidate;bestMargin=margin;}
    }
    out.searchExhausted=!capped;
    if(!found||!awake())return false;
    // Re-evaluate after the entire bounded search; no stale clock/FP acceptance.
    scalar finalMargin=0;if(!verify(best,finalMargin)||!awake())return false;
    out.candidate=best;out.minimumMargin=finalMargin;result=out;return true;
}
}}
#endif
