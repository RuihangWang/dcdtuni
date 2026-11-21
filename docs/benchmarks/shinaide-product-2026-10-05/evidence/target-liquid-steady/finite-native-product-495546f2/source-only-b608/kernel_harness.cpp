
#define DCCART_FINITE_REPAIR_KERNEL_ONLY
#include "dcCartFinitePointRepair.H"
#include <iostream>
#include <iomanip>
#include <string>
using namespace dcCartFiniteRepairKernel;
int main()
{
    std::cout<<std::setprecision(17);
    std::string command;std::cin>>command;
    if (command=="project")
    {
        unsigned n,m,updates;std::cin>>n>>m>>updates;
        Row desired(n),b(m);Matrix a(m,Row(n));
        for(double& x:desired) std::cin>>x;
        for(unsigned i=0;i<m;++i) {for(double& x:a[i])std::cin>>x;std::cin>>b[i];}
        auto out=project(desired,a,b,updates);
        std::cout<<out.feasible<<' '<<out.updates;
        for(double x:out.step)std::cout<<' '<<x;
        std::cout<<'\n';return 0;
    }
    if (command=="select")
    {
        unsigned n,m;int target;std::cin>>n>>m>>target;
        std::vector<XYZ> p(n);std::vector<Face> f(m);
        std::vector<std::vector<int>> pf(n);
        for(auto& x:p)for(double& v:x)std::cin>>v;
        for(unsigned i=0;i<m;++i)
        {
            unsigned k;int protectedFace;std::cin>>k>>protectedFace;
            f[i].protectedFace=protectedFace;f[i].points.resize(k);
            for(int& v:f[i].points){std::cin>>v;pf[v].push_back(i);}
        }
        const auto mode=select(p,f,pf,target);
        std::cout<<mode.dimension<<' '<<mode.pivot<<' '<<mode.pairA<<' '<<mode.pairB<<' '<<mode.axis<<'\n';
        return 0;
    }
    if (command=="propose")
    {
        unsigned n;std::cin>>n;
        auto evaluation=[](const Row& q)
        {
            std::cout<<"eval";
            for(double x:q)std::cout<<' '<<x;
            std::cout<<std::endl;
            Evaluation e;unsigned count;int valid;
            std::cin>>valid>>e.objective>>count;
            e.valid=valid;e.constraints.resize(count);
            for(double& x:e.constraints)std::cin>>x;
            if (!std::cin) e.valid=false;
            return e;
        };
        auto out=propose(n,evaluation,[](){return true;});
        std::cout<<"done "<<out.requests<<' '<<out.iterations<<' '<<out.improved;
        for(double x:out.parameters)std::cout<<' '<<x;
        std::cout<<std::endl;return 0;
    }
    if (command=="edge")
    {
        auto empty=project({}, {}, {});
        auto invalid=project({1}, {{std::numeric_limits<double>::quiet_NaN()}}, {1});
        auto unconstrained=project({1,2}, {}, {});
        auto cap=project({-1}, {{1}}, {0},0);
        auto zero=project({1},{{0}},{-1});
        auto budget=propose(3,[](const Row&){return Evaluation{};},[](){return false;});
        std::cout<<empty.feasible<<invalid.feasible<<unconstrained.feasible
                 <<cap.feasible<<zero.feasible<<' '<<budget.requests<<'\n';return 0;
    }

    if (command=="roles")
    {
        unsigned n;double limit;int upper;std::cin>>n>>limit>>upper;
        Row a(n),b(n);
        for(double& x:a)std::cin>>x;
        for(double& x:b)std::cin>>x;
        std::cout<<preservesHardRoles(a,b,limit,bool(upper),1e-300)<<'\n';return 0;
    }
    if (command=="zones")
    {
        std::set<int> cells{1,2,8};
        std::vector<std::vector<int>> noZones,core{{1}},outside{{8}},other{{3,4}};
        std::cout<<intersectsCellZones(cells,noZones)<<intersectsCellZones(cells,core)
                 <<intersectsCellZones(cells,outside)<<intersectsCellZones(cells,other)<<'\n';
        return 0;
    }
    return 2;
}
