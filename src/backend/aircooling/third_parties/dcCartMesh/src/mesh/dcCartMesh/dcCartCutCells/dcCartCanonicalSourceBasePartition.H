#ifndef dcCartCanonicalSourceBasePartition_H
#define dcCartCanonicalSourceBasePartition_H
#include "dcCartPrefixSourceRingRestoration.H"

namespace Foam { namespace dcCartCanonicalSourceBasePartition
{
namespace S=dcCartSharedAxisSeamVolumePartition;
namespace R=dcCartPrefixSourceRingRestoration;
namespace G=dcCartSourcePlanarConeGroups;
namespace E=dcCartExactBinary64Geometry;
using Ring=std::vector<label>;
// Proposal only. Preserve every source endpoint/role and every existing row
// vertex. No source permission, peer CC/V, quality or publication is granted.
template<class Mesh>
bool partition(const Mesh& raw,const S::Plan& input,
 const std::function<bool()>& available,S::Plan& out,
 std::size_t& changed,std::string* why=nullptr)
{
    out=S::Plan();changed=0;
    auto work=[&](){return R::work(available);};
    auto fail=[&](const char* reason){if(why)*why=reason;return false;};
    const std::size_t bound=std::numeric_limits<label>::max();
    if(!input.ready||input.rows.empty()||raw.faces.size()>bound
        ||raw.points.size()>bound||input.births.size()>bound-raw.points.size())
        return fail("canonical-input");
    struct Points
    {
        const Mesh& raw;const S::Plan& plan;
        const point& at(std::size_t p)const{return S::coordinate(raw,plan,label(p));}
        std::size_t size()const{return raw.points.size()+plan.births.size();}
    };
    struct View {Points points;} view{{raw,input}};
    auto lessPoint=[&](label a,label b)
    {
        const point& x=view.points.at(a);const point& y=view.points.at(b);
        if(x.x()!=y.x())return x.x()<y.x();
        if(x.y()!=y.y())return x.y()<y.y();
        return x.z()<y.z();
    };
    auto canonicalCycle=[&](const Ring& poly)
    {
        label bestStart=0,bestDirection=1;bool haveBest=false;
        for(label direction:{label(1),label(-1)})for(label start=0;start<label(poly.size());++start)
        {
            bool better=false;
            if(haveBest)for(label j=0;j<label(poly.size());++j)
            {
                const label candidate=poly[(start+direction*j+label(poly.size())*2)%poly.size()];
                const label current=poly[(bestStart+bestDirection*j+label(poly.size())*2)%poly.size()];
                if(lessPoint(candidate,current)){better=true;break;}
                if(lessPoint(current,candidate))break;
            }
            if(!haveBest||better){bestStart=start;bestDirection=direction;haveBest=true;}
        }
        Ring best;best.reserve(poly.size());
        for(label j=0;j<label(poly.size());++j)
            best.push_back(poly[(bestStart+bestDirection*j+label(poly.size())*2)%poly.size()]);
        return best;
    };
    auto writable=[&](const Ring& poly)
    {
        vector n=vector::zero;point c=point::zero;const label k=poly.size();
        if(k<3)return false;
        // Literal polygonNormalCentre/writablePolygon production arithmetic.
        for(label i=0;i<k;++i)
        {
            const point& a=view.points.at(poly[i]);const point& b=view.points.at(poly[(i+1)%k]);
            n.x()+=(a.y()-b.y())*(a.z()+b.z());
            n.y()+=(a.z()-b.z())*(a.x()+b.x());
            n.z()+=(a.x()-b.x())*(a.y()+b.y());c+=a;
        }
        n*=.5;c/=scalar(k);
        return S::finite(n)&&S::finite(c)&&mag(n)>1e-12;
    };
    label next=label(raw.faces.size());std::set<label> ids;
    for(const auto& row:input.rows)
    {
        if(!work())return fail("canonical-clock");
        if(row.native<0||row.native==std::numeric_limits<label>::max()
            ||!ids.insert(row.native).second)return fail("canonical-native");
        next=std::max(next,label(row.native+1));
    }
    std::vector<S::Row> rows;rows.reserve(input.rows.size());
    for(const auto& row:input.rows)
    {
        if(!work())return fail("canonical-clock");
        if(row.ring.size()<3||row.ring.size()>64)return fail("canonical-ring");
        std::set<label> unique;
        for(label p:row.ring)
            if(p<0||std::size_t(p)>=view.points.size()||!unique.insert(p).second
                ||!S::finite(view.points.at(p)))return fail("canonical-point");
        // Only source bases with a genuine redundant straight edge node are
        // proposed for a positive original-vertex fan; caps remain unchanged.
        if(row.source<0||row.ring.size()==3){rows.push_back(row);continue;}
        if(std::size_t(row.source)>=raw.faces.size()||!raw.faceAlive[row.source]
            ||row.patch!=raw.patch[row.source]||row.zone!=raw.zone[row.source])
            return fail("canonical-source");
        std::vector<G::Point> localPoints;G::Ring localRing;
        localPoints.reserve(row.ring.size());localRing.reserve(row.ring.size());
        for(label p:row.ring)
        {const point& x=view.points.at(p);localRing.push_back(localPoints.size());localPoints.push_back({{x.x(),x.y(),x.z()}});}
        E::Context context(work);E::Vector exactNormal,origin;
        const bool planarConvex=G::Detail::plane(localRing,localPoints,exactNormal,origin,context);
        if(!context.good())return fail("canonical-exact-or-clock");
        if(!planarConvex){rows.push_back(row);continue;}
        bool collinear=false;
        for(std::size_t j=0;j<localPoints.size();++j)
        {
            const auto x=E::vector(localPoints[j],context);
            const auto a=E::difference(E::vector(localPoints[(j+localPoints.size()-1)%localPoints.size()],context),x,context);
            const auto b=E::difference(E::vector(localPoints[(j+1)%localPoints.size()],context),x,context);
            const auto cross=E::cross(a,b,context);
            if(!E::sign(cross[0])&&!E::sign(cross[1])&&!E::sign(cross[2])
                &&E::sign(E::dot(a,b,context))<0)collinear=true;
        }
        if(!context.good())return fail("canonical-exact-or-clock");
        if(!collinear){rows.push_back(row);continue;}
        const Ring poly=canonicalCycle(row.ring);vector av,original;point fc,originalFc;
        S::metric(raw,input,poly,av,fc);S::metric(raw,input,row.ring,original,originalFc);
        const scalar amount=mag(av);
        if(!S::finite(av)||!S::finite(fc)||!S::finite(original)||!S::finite(originalFc)
            ||!std::isfinite(amount)||amount<=VSMALL)return fail("canonical-metric");
        const vector normal=av/amount;std::vector<Ring> pieces;
        // Literal positive original-vertex fan proposal; never create an FC.
        for(std::size_t k=0;k<poly.size();++k)
        {
            if(!work())return fail("canonical-clock");
            std::vector<Ring> trial;bool positive=true;
            for(std::size_t j=1;j+1<poly.size();++j)
            {
                const label a=poly[k],b=poly[(k+j)%poly.size()],c=poly[(k+j+1)%poly.size()];
                const scalar area=(((view.points.at(b)-view.points.at(a))^(view.points.at(c)-view.points.at(a)))&normal)*.5;
                if(!std::isfinite(area)||area<=1e-15*amount||!writable({a,b,c}))
                {positive=false;break;}
                trial.push_back({a,b,c});
            }
            if(positive){pieces=std::move(trial);break;}
        }
        if(pieces.empty()){rows.push_back(row);continue;}
        if((av&original)<0)for(auto& piece:pieces)std::reverse(piece.begin()+1,piece.end());
        vector sumSf=vector::zero;point areaMoment=point::zero;scalar sumArea=0;
        R::Detail::Integrals exactTotal;
        const auto exactOriginal=R::Detail::integrals(view,row.ring,context);
        for(const Ring& piece:pieces)
        {
            if(!work())return fail("canonical-clock");
            vector sf;point centre;S::metric(raw,input,piece,sf,centre);const scalar area=mag(sf);
            if(!S::finite(sf)||!S::finite(centre)||!std::isfinite(area)||area<=VSMALL
                ||!std::isfinite(sf&original)||(sf&original)<=0||!writable(piece))
                return fail("canonical-piece");
            sumSf+=sf;sumArea+=area;areaMoment+=area*centre;
            R::Detail::add(exactTotal,R::Detail::integrals(view,piece,context),context);
        }
        const scalar originalArea=mag(original);
        const scalar scale=std::max(std::sqrt(originalArea),scalar(1e-6));
        if(!context.good()||!R::Detail::same(exactOriginal,exactTotal,context))
            return fail("canonical-exact-source-moment");
        if(!S::finite(sumSf)||!S::finite(areaMoment)||!std::isfinite(sumArea)||originalArea<=VSMALL
            ||mag(sumSf-original)/originalArea>1e-10
            ||mag(sumArea-originalArea)/originalArea>1e-10
            ||mag(areaMoment/std::max(sumArea,scalar(VSMALL))-originalFc)/scale>1e-10)
            return fail("canonical-source-area-moment");
        for(std::size_t j=0;j<pieces.size();++j)
        {
            if(rows.size()>=bound)return fail("canonical-row-count");
            S::Row part=row;
            if(j)
            {
                if(next==std::numeric_limits<label>::max())return fail("canonical-face-count");
                part.native=next++;
            }
            part.ring=std::move(pieces[j]);rows.push_back(std::move(part));
        }
        ++changed;
    }
    if(!work())return fail("canonical-clock");
    S::Plan proposal=input;proposal.rows=std::move(rows);out=std::move(proposal);return true;
}
} }
#endif
