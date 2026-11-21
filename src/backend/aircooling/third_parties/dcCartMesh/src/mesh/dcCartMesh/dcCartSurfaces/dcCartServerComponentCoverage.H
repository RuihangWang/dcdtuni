#ifndef dcCartServerComponentCoverage_H
#define dcCartServerComponentCoverage_H
// Exact four-face coverage only. Existing server policy is checked by the caller.
// No point/triangle changes, signed closed cycle, quality waiver, clock or MPI.
#include "dcCartExactBinary64Geometry.H"
#include <string>
#include <set>
#include <iterator>
namespace dcCartServerComponentCoverage
{
namespace E=dcCartExactBinary64Geometry;
constexpr std::size_t maxTriangles=128,maxOperations=262144;
using Id=std::int64_t;
using Members=std::map<Id,std::vector<Id>>;
enum Role {front=0,outlet=1,top=2,bottom=3,unknown=4};
struct Facet
{
    Id surface=-1,triangle=-1,seed=-1;
    std::array<Id,3> originalPoints{{-1,-1,-1}};
    std::array<E::Point,3> points;
    std::string objectUid,faceUid;
    Role role=unknown;
};
struct Result
{
    bool complete=false;
    std::size_t operations=0;
    E::Refusal arithmeticRefusal=E::Refusal::none;
    const char* reason="unprepared";
    std::array<E::Point,2> bounds{};
    std::vector<Id> seeds;
};
inline Role faceRole(const std::string& uid)
{
    const auto slash=uid.find_last_of('/');
    if(slash==std::string::npos)return unknown;
    const std::string leaf=uid.substr(slash+1);
    const char* names[]={"inlet","outlet","top","bottom"};
    for(unsigned r=0;r<4;++r)
    {
        const std::string name(names[r]);
        if(leaf==name+"___face__"||leaf==name+"___wall__"
         || (leaf.compare(0,name.size()+3,name+"_p_")==0
             && leaf.size()>name.size()+3))return Role(r);
    }
    return unknown;
}
inline int compare(const E::Dyadic& a,const E::Dyadic& b,E::Context& c)
{return E::sign(E::subtract(a,b,c));}
using UV=std::array<E::Dyadic,2>;
using TriangleUV=std::array<UV,3>;
inline E::Dyadic det(const UV& a,const UV& b,E::Context& c)
{return E::subtract(E::multiply(a[0],b[1],c),E::multiply(a[1],b[0],c),c);}
inline UV minus(const UV& a,const UV& b,E::Context& c)
{return UV{{E::subtract(a[0],b[0],c),E::subtract(a[1],b[1],c)}};}
inline bool interiorOverlap(const TriangleUV& a,const TriangleUV& b,E::Context& c)
{
    for(const auto* triangle:{&a,&b})for(unsigned e=0;e<3;++e)
    {
        const UV edge=minus((*triangle)[(e+1)%3],(*triangle)[e],c);
        E::Dyadic amin=det(edge,a[0],c),amax=amin,bmin=det(edge,b[0],c),bmax=bmin;
        for(unsigned k=1;k<3;++k)
        {
            const auto av=det(edge,a[k],c),bv=det(edge,b[k],c);
            if(compare(av,amin,c)<0)amin=av;if(compare(av,amax,c)>0)amax=av;
            if(compare(bv,bmin,c)<0)bmin=bv;if(compare(bv,bmax,c)>0)bmax=bv;
        }
        if(!c.good())return false;
        if(compare(amax,bmin,c)<=0||compare(bmax,amin,c)<=0)return false;
    }
    return c.good(); // strict interior overlap; edge and T contacts are allowed.
}
inline bool nonzero(const E::Vector& v)
{return E::sign(v[0])||E::sign(v[1])||E::sign(v[2]);}
inline bool positiveSegment(const E::Vector& a,const E::Vector& b,
    const E::Vector& d,const E::Vector& e,E::Context& c)
{
    const auto ab=E::difference(b,a,c),de=E::difference(e,d,c);
    if(!nonzero(ab)||!nonzero(de))return false;
    if(nonzero(E::cross(ab,de,c))||nonzero(E::cross(ab,E::difference(d,a,c),c)))return false;
    unsigned axis=0;while(axis<3&&!E::sign(ab[axis]))++axis;
    if(axis==3||!c.good())return false;
    const auto amin=compare(a[axis],b[axis],c)<0?a[axis]:b[axis];
    const auto amax=compare(a[axis],b[axis],c)>0?a[axis]:b[axis];
    const auto dmin=compare(d[axis],e[axis],c)<0?d[axis]:e[axis];
    const auto dmax=compare(d[axis],e[axis],c)>0?d[axis]:e[axis];
    const auto lo=compare(amin,dmin,c)>0?amin:dmin;
    const auto hi=compare(amax,dmax,c)<0?amax:dmax;
    return c.good()&&compare(lo,hi,c)<0; // single-point touch cannot link seeds.
}
inline Result certify(const std::vector<Facet>& rows,const Members& originalMembers,
    std::size_t operationLimit=maxOperations)
{
    Result result;E::Context c([](){return true;},std::min(operationLimit,maxOperations));
    const auto fail=[&](const char* reason)->Result
    {result.reason=reason;result.operations=c.operations;result.arithmeticRefusal=c.refusal;return result;};
    {
        if(rows.empty()||rows.size()>maxTriangles)return fail("triangle-cap");
        Members seenMembers;std::set<Id> triangles;std::map<Id,E::Point> originalPoints;
        std::vector<std::array<E::Vector,3>> points;points.reserve(rows.size());
        E::Point lo=rows.front().points[0],hi=lo;
        const Id surface=rows.front().surface;const std::string uid=rows.front().objectUid;
        if(surface<0||uid.empty())return fail("source-identity");
        for(const auto& row:rows)
        {
            if(!c.take())return fail("operation-cap");
            if(row.surface!=surface||row.objectUid!=uid||row.faceUid.empty()
             ||unsigned(row.role)>=4||row.role!=faceRole(row.faceUid)||row.seed<0||row.triangle<0
             ||!triangles.insert(row.triangle).second)return fail("source-identity-or-duplicate");
            seenMembers[row.seed].push_back(row.triangle);
            std::array<E::Vector,3> exact;
            for(unsigned v=0;v<3;++v)
            {
                if(row.originalPoints[v]<0)return fail("point-id");
                const auto old=originalPoints.find(row.originalPoints[v]);
                if(old!=originalPoints.end()&&old->second!=row.points[v])return fail("point-id-conflict");
                originalPoints[row.originalPoints[v]]=row.points[v];
                exact[v]=E::vector(row.points[v],c);
                for(unsigned d=0;d<3;++d)
                {lo[d]=std::min(lo[d],row.points[v][d]);hi[d]=std::max(hi[d],row.points[v][d]);}
            }
            if(!c.good())return fail("exact-arithmetic-refusal");points.push_back(exact);
        }
        for(unsigned d=0;d<3;++d)if(!std::isfinite(lo[d])||!std::isfinite(hi[d])||!std::isfinite(hi[d]-lo[d])||!(hi[d]-lo[d]>1e-6))return fail("degenerate-envelope");
        for(auto& pair:seenMembers)std::sort(pair.second.begin(),pair.second.end());
        Members expected=originalMembers;std::set<Id> declared;
        for(auto& pair:expected)
        {
            if(pair.first<0||pair.second.empty())return fail("declared-membership");
            std::sort(pair.second.begin(),pair.second.end());
            for(const auto t:pair.second)if(t<0||!declared.insert(t).second)return fail("declared-duplicate");
        }
        if(expected!=seenMembers)return fail("incomplete-original-membership");
        std::array<int,4> axes{{-1,-1,-1,-1}},sides{{-1,-1,-1,-1}},winding{{0,0,0,0}};
        std::array<std::vector<std::size_t>,4> faceRows;
        std::vector<TriangleUV> uv(rows.size());std::vector<E::Dyadic> twice(rows.size());
        for(std::size_t i=0;i<rows.size();++i)
        {
            const auto& row=rows[i];const unsigned role=unsigned(row.role);int axis=-1;
            for(unsigned d=0;d<3;++d)if(row.points[0][d]==row.points[1][d]&&row.points[0][d]==row.points[2][d])
            {if(axis>=0)return fail("degenerate-triangle");axis=int(d);}
            if(axis<0)return fail("non-axis-aligned-face");
            const double plane=row.points[0][axis];const int side=plane==lo[axis]?0:(plane==hi[axis]?1:-1);
            if(side<0|| (axes[role]>=0&&(axes[role]!=axis||sides[role]!=side)))return fail("non-envelope-face");
            axes[role]=axis;sides[role]=side;unsigned dims[2],n=0;
            for(unsigned d=0;d<3;++d)if(int(d)!=axis)dims[n++]=d;
            for(unsigned v=0;v<3;++v)uv[i][v]=UV{{points[i][v][dims[0]],points[i][v][dims[1]]}};
            twice[i]=det(minus(uv[i][1],uv[i][0],c),minus(uv[i][2],uv[i][0],c),c);
            const int sign=E::sign(twice[i]);
            if(!sign||(winding[role]&&winding[role]!=sign))return fail("zero-or-inconsistent-original-winding");
            winding[role]=sign;faceRows[role].push_back(i);
        }
        if(axes[front]<0||axes[top]<0||axes[front]==axes[top]
         ||axes[front]!=axes[outlet]||sides[front]==sides[outlet]
         ||axes[top]!=axes[bottom]||sides[top]==sides[bottom])return fail("not-four-required-opposed-faces");
        const auto elo=E::vector(lo,c),ehi=E::vector(hi,c),span=E::difference(ehi,elo,c);
        for(unsigned role=0;role<4;++role)
        {
            E::Dyadic sum;unsigned dims[2],n=0;
            for(unsigned d=0;d<3;++d)if(int(d)!=axes[role])dims[n++]=d;
            for(std::size_t a=0;a<faceRows[role].size();++a)
            {
                const auto ai=faceRows[role][a];auto area=twice[ai];if(area.n<0)area.n=-area.n;
                sum=E::add(sum,area,c);
                for(std::size_t k=a+1;k<faceRows[role].size();++k)
                    if(interiorOverlap(uv[ai],uv[faceRows[role][k]],c))return fail("positive-area-overlap");
            }
            const auto want=E::multiply(E::number(2.0,c),E::multiply(span[dims[0]],span[dims[1]],c),c);
            if(!c.good())return fail("exact-arithmetic-refusal");
            if(compare(sum,want,c)!=0)return fail("incomplete-face");
        }
        std::map<Id,Id> parent;
        for(const auto& pair:expected)parent[pair.first]=pair.first;
        const auto find=[&](Id x)->Id {while(parent[x]!=x)x=parent[x];return x;};
        for(auto a=parent.begin();a!=parent.end();++a)for(auto b=std::next(a);b!=parent.end();++b)
        {
            bool linked=false;
            for(std::size_t i=0;i<rows.size()&&!linked;++i)if(rows[i].seed==a->first)
              for(std::size_t j=0;j<rows.size()&&!linked;++j)if(rows[j].seed==b->first)
                for(unsigned e=0;e<3&&!linked;++e)for(unsigned f=0;f<3&&!linked;++f)
                  linked=positiveSegment(points[i][e],points[i][(e+1)%3],points[j][f],points[j][(f+1)%3],c);
            if(!c.good())return fail("exact-arithmetic-refusal");
            if(linked){const Id ra=find(a->first),rb=find(b->first);parent[std::max(ra,rb)]=std::min(ra,rb);}
        }
        std::set<Id> roots;for(const auto& pair:parent)roots.insert(find(pair.first));
        if(roots.size()!=1)return fail("disjoint-or-point-contact-seeds");
        if(!c.good())return fail("exact-arithmetic-refusal");
        result.complete=true;result.reason="four-faces-exactly-covered";result.bounds={{lo,hi}};
        for(const auto& pair:expected)result.seeds.push_back(pair.first);
        result.operations=c.operations;return result;
    }
}
}
#endif
