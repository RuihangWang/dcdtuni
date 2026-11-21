#ifndef dcCartCompleteCavityTwoLobe_H
#define dcCartCompleteCavityTwoLobe_H
#include "dcCartCompleteCavityPreparedTopology.H"
namespace Foam { namespace dcCartCompleteCavityTwoLobe
{
namespace P=dcCartCompleteCavityPreparedTopology;namespace O=dcCartCompleteCavityLocalOracle;
using World=std::tuple<scalar,scalar,scalar>;
struct Base {label native=-1;std::vector<label> outward;};
struct Lobe {std::vector<Base> bases;};
struct Discovery {label supports=0,partitions=0,planeBases=0;bool timeout=false;};
inline World world(const point& p){return std::make_tuple(p.x(),p.y(),p.z());}
inline bool geometryKey(const O::View& v,const std::vector<label>& ring,std::vector<World>& key)
{
    key.clear();
    for(int direction:{1,-1})for(std::size_t start=0;start<ring.size();++start)
    {
        std::vector<World> trial;
        for(std::size_t k=0;k<ring.size();++k)
        {
            auto a=P::alias(v,ring[(start+ring.size()*2+direction*int(k))%ring.size()]);
            if(!a||!O::finite(a->world))return false;trial.push_back(world(a->world));
        }
        if(key.empty()||trial<key)key=std::move(trial);
    }
    return !key.empty();
}
inline bool loop(const O::View& v,const Lobe& lobe,direction axis,scalar value,
                 std::vector<label>& separator)
{
    separator.clear();
    std::map<std::pair<label,label>,std::vector<std::pair<label,label>>> edges;
    for(const auto& b:lobe.bases)for(std::size_t k=0;k<b.outward.size();++k)
    {
        label a=b.outward[k],n=b.outward[(k+1)%b.outward.size()];edges[std::minmax(a,n)].emplace_back(a,n);
    }
    std::map<label,label> out;std::set<label> incoming;
    for(const auto& edge:edges)
    {
        const auto& rows=edge.second;
        if(rows.size()==2){if(rows[0].first!=rows[1].second||rows[0].second!=rows[1].first)return false;continue;}
        if(rows.size()!=1)return false;
        label a=rows[0].second,b=rows[0].first;
        const auto x=P::alias(v,a),y=P::alias(v,b);
        if(!x||!y||x->world[axis]!=value||y->world[axis]!=value||!out.emplace(a,b).second||!incoming.insert(b).second)return false;
    }
    if(out.size()!=4||incoming.size()!=4)return false;
    label start=out.begin()->first;
    for(const auto& edge:out)if(world(P::alias(v,edge.first)->world)<world(P::alias(v,start)->world))start=edge.first;
    label p=start;for(int k=0;k<4;++k){if(!out.count(p))return false;separator.push_back(p);p=out.at(p);}
    return p==start&&std::set<label>(separator.begin(),separator.end()).size()==4;
}
inline bool sameCycle(const std::vector<label>& a,const std::vector<label>& b,bool reverse)
{
    if(a.size()!=b.size())return false;
    for(std::size_t start=0;start<b.size();++start)
    {
        bool same=true;
        for(std::size_t k=0;k<a.size();++k)
            same&=a[k]==b[(start+b.size()*2+(reverse?-int(k):int(k)))%b.size()];
        if(same)return true;
    }
    return false;
}
inline bool solve4(std::array<std::array<scalar,5>,4> a,std::array<scalar,4>& x)
{
    for(int k=0;k<4;++k)
    {
        int pivot=k;for(int i=k+1;i<4;++i)if(mag(a[i][k])>mag(a[pivot][k]))pivot=i;
        if(!O::finite(a[pivot][k])||mag(a[pivot][k])<=1e-14)return false;
        std::swap(a[pivot],a[k]);
        const scalar d=a[k][k];for(int j=k;j<5;++j)a[k][j]/=d;
        for(int i=0;i<4;++i)if(i!=k){const scalar q=a[i][k];for(int j=k;j<5;++j)a[i][j]-=q*a[k][j];}
    }
    for(int i=0;i<4;++i){x[i]=a[i][4];if(!O::finite(x[i]))return false;}return true;
}
// Proposal generation only. A finite four-active-plane Chebyshev seed never
// replaces the exact/installed complete-cavity guard in PreparedTopology.
inline bool seed(const O::View& view,const Lobe& lobe,const std::function<bool()>& awake,
                 point& result,Discovery& stats)
{
    result=point::zero;
    if(lobe.bases.size()<4||lobe.bases.size()>32)return false;
    std::vector<std::array<scalar,4>> planes;
    for(const auto& b:lobe.bases)
    {
        vector sf(Zero);point fc(Zero);
        if(b.native>=0)
        {
            const O::Face* f=nullptr;for(const auto& row:view.faces)if(row.native==b.native){f=&row;break;}
            if(!f||b.outward.size()!=f->ring.size())return false;
            sf=f->area;fc=f->centre;
            if(!sameCycle(b.outward,f->ring,false))sf=-sf;
        }
        else
        {
            if(b.outward.size()!=4)return false;
            face f(4);pointField pts(4);
            for(label i=0;i<4;++i){auto a=P::alias(view,b.outward[i]);if(!a)return false;pts[i]=a->world;f[i]=i;}
            // Uses installed polygon arithmetic, not inferred support coords.
            sf=f.areaNormal(pts);fc=f.centre(pts);
        }
        const scalar area=mag(sf);if(!O::finite(sf)||!O::finite(fc)||!O::finite(area)||area<=1e-12)return false;
        const vector n=sf/area;planes.push_back({{n.x(),n.y(),n.z(),n&fc}});
    }
    bool found=false;scalar radius=0;point best(Zero);
    const std::size_t n=planes.size();
    for(std::size_t a=0;a+3<n;++a)for(std::size_t b=a+1;b+2<n;++b)
      for(std::size_t c=b+1;c+1<n;++c)for(std::size_t d=c+1;d<n;++d)
    {
        if((stats.planeBases&63)==0&&!awake()){stats.timeout=true;return false;}
        if(stats.planeBases>=65536)return false;++stats.planeBases;
        const std::array<std::size_t,4> indices{{a,b,c,d}};std::array<std::array<scalar,5>,4> system;
        for(int row=0;row<4;++row){const auto& p=planes[indices[row]];system[row]={{p[0],p[1],p[2],1,p[3]}};}
        std::array<scalar,4> q;if(!solve4(system,q)||q[3]<=3e-6)continue;
        bool feasible=true;for(const auto& p:planes)
            if(p[3]-(p[0]*q[0]+p[1]*q[1]+p[2]*q[2]+q[3])<-2e-12){feasible=false;break;}
        const point candidate(q[0],q[1],q[2]);
        if(feasible&&(!found||q[3]>radius+2e-12||(mag(q[3]-radius)<=2e-12&&world(candidate)<world(best))))
        {found=true;radius=q[3];best=candidate;}
    }
    if(!found||!awake())return false;result=best;return true;
}
inline bool generate(const O::View& view,const O::Cut& live,const std::function<bool()>& awake,
                     P::Recipe& out,Discovery& stats)
{
    out=P::Recipe();stats=Discovery();
    if(!view.ready()||view.core.size()>O::Caps::core||!awake())return false;
    try
    {
        std::set<label> core(view.core.begin(),view.core.end());std::vector<Base> shell;
        std::vector<std::pair<std::vector<World>,Base>> keyed;
        for(const auto& f:view.faces)
        {
            const bool a=core.count(f.owner),b=f.internal&&core.count(f.neighbour);
            if(a==b)continue;
            if(O::protectedRole(f))return false;
            Base base;base.native=f.native;base.outward=f.ring;if(!a)std::reverse(base.outward.begin(),base.outward.end());
            std::vector<World> key;if(!geometryKey(view,base.outward,key))return false;
            keyed.emplace_back(std::move(key),std::move(base));
        }
        if(keyed.size()<6||keyed.size()>62)return false;
        std::sort(keyed.begin(),keyed.end(),[](const auto& a,const auto& b){return a.first<b.first;});
        for(std::size_t i=0;i<keyed.size();++i)
        {if(i&&keyed[i].first==keyed[i-1].first)return false;shell.push_back(std::move(keyed[i].second));}
        std::set<std::pair<direction,scalar>> supports;
        for(const auto& base:shell)for(direction axis=0;axis<3;++axis)
        {
            const auto first=P::alias(view,base.outward.front());if(!first)return false;
            bool flat=true;const scalar value=first->world[axis];
            for(label p:base.outward){auto a=P::alias(view,p);if(!a)return false;flat&=a->world[axis]==value;}
            if(flat)supports.emplace(axis,value);
        }
        if(supports.size()>64)return false;
        for(const auto& plane:supports)
        {
            if(!awake()){stats.timeout=true;return false;}++stats.supports;
            const direction axis=plane.first;const scalar value=plane.second;Lobe left,right;
            bool possible=true;
            for(const auto& base:shell)
            {
                bool low=false,high=false;
                for(label p:base.outward){const scalar x=P::alias(view,p)->world[axis];low|=x<value;high|=x>value;}
                if(low&&high){possible=false;break;}
                if(!low&&!high)
                {
                    const O::Face* f=nullptr;for(const auto& row:view.faces)if(row.native==base.native){f=&row;break;}
                    const label inner=core.count(f->owner)?f->owner:f->neighbour;
                    const O::Cell* c=nullptr;for(const auto& row:view.cells)if(row.native==inner){c=&row;break;}
                    if(!c||c->centre[axis]==value){possible=false;break;}
                    low=c->centre[axis]<value;high=!low;
                }
                (low?left:right).bases.push_back(base);
            }
            if(!possible||left.bases.size()<3||right.bases.size()<3||left.bases.size()>31||right.bases.size()>31)continue;
            std::vector<label> a,b;
            if(!loop(view,left,axis,value,a)||!loop(view,right,axis,value,b)||!sameCycle(a,b,true))continue;
            left.bases.push_back({-1,a});right.bases.push_back({-1,b});++stats.partitions;
            point ap[2];if(!seed(view,left,awake,ap[0],stats)||!seed(view,right,awake,ap[1],stats))continue;
            P::Recipe r;r.appendedPoints={ap[0],ap[1]};
            const std::size_t count=left.bases.size()+right.bases.size();
            if(count>O::Caps::core)return false;
            for(std::size_t i=0;i<count;++i)r.replacementCells.push_back(i<view.core.size()?view.core[i]:live.nCells+i-view.core.size());
            const label sepLeft=r.replacementCells[left.bases.size()-1],sepRight=r.replacementCells.back();
            r.internalFaces.push_back({a,sepLeft,sepRight});
            std::size_t next=0;label lobe=0;
            for(const Lobe* l:{&left,&right})
            {
                std::map<std::pair<label,label>,std::vector<std::tuple<label,label,label>>> edges;
                for(const auto& base:l->bases)
                {
                    label child=r.replacementCells[next++];if(base.native>=0)r.exteriorCell.emplace(base.native,child);
                    for(std::size_t k=0;k<base.outward.size();++k)
                    {label p=base.outward[k],q=base.outward[(k+1)%base.outward.size()];edges[std::minmax(p,q)].emplace_back(child,p,q);}
                }
                for(const auto& edge:edges)
                {
                    if(edge.second.size()!=2)return false;
                    label c,p,q,d,x,y;std::tie(c,p,q)=edge.second[0];std::tie(d,x,y)=edge.second[1];
                    if(p!=y||q!=x||c==d)return false;
                    r.internalFaces.push_back({{q,p,label(live.points.size()+lobe)},c,d});
                }
                ++lobe;
            }
            if(r.internalFaces.size()>O::Caps::faces)return false;
            out=std::move(r);return true; // one bounded geometry seed; no pool widening
        }
        return false;
    }
    catch(...){out=P::Recipe();return false;}
}
} }
#endif
