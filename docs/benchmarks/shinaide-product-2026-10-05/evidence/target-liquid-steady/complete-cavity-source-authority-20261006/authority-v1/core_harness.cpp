#include "fixture.H"
#include <iostream>
using L=dcCartCompleteCavityOriginCore::Lineage<int>;
int main(int argc,char** argv)
{
    try {
        require(argc==2,"case");std::string name=argv[1];auto s=boxes();auto o=records(s);auto p=proof();
        auto yes=[](){return true;};auto no=[](){return false;};
        auto verify=[&](){return verifyMany(s,o,p,0,yes);};
        if(name=="five_origins") require(verify(),"complete adjacent normal five-origin shapes");
        else if(name=="same_origin_not_duplicate_input") {
            L l;require(l.initialize(5,[](int c){return c<5;},yes),"init");
            l.inherit(5,0,yes);require(l.join(0,5,yes),"same-parent ancestry dedup");
            std::vector<int> ids;require(l.originsForCells({0},ids)&&ids==std::vector<int>{0},"one source");
        }
        else if(name=="duplicate_origin") {o[4]=o[0];require(!verify(),"duplicate refused");}
        else if(name=="missing_facet") {o[0].facets.pop_back();require(!verify(),"missing refused");}
        else if(name=="facet_order") {std::swap(o[0].facets[0],o[0].facets[1]);require(!verify(),"ordered roles");}
        else if(name=="unknown_patch") {o[0].facets[0][1]=7;require(!verify(),"patch role bound");}
        else if(name=="wrong_zone") {o[0].facets[0][2]=3;require(!verify(),"zone role bound");}
        else if(name=="wrong_rank") {std::get<0>(o[0].identity)=1;require(!verify(),"nonlocal");}
        else if(name=="wrong_leaf") {std::get<3>(o[0].identity)++;require(!verify(),"normal leaf");}
        else if(name=="wrong_piece") {std::get<4>(o[0].identity)++;require(!verify(),"normal split identity");}
        else if(name=="wrong_level") {std::get<2>(o[0].identity)++;require(!verify(),"normal level");}
        else if(name=="stale_raw") {p.raw[0]='f';require(!verify(),"fresh raw");}
        else if(name=="stale_roles") {p.roles[0]='f';require(!verify(),"fresh roles");}
        else if(name=="stale_material") {p.material[0]='f';require(!verify(),"fresh material");}
        else if(name=="stale_producer") {p.producer[0]='f';require(!verify(),"fresh producer");}
        else if(name=="stale_boundary") {p.boundaryMembership[0]='f';require(!verify(),"fresh boundary");}
        else if(name=="unknown_material") {p.materialKey.clear();require(!verify(),"material absent");}
        else if(name=="incomplete") {s.complete=false;require(!verify(),"partial snapshot");}
        else if(name=="alternate_path") {s.completedFluidOnlyPath=false;require(!verify(),"unknown path");}
        else if(name=="nonlocal_valid") {s.valid[0]=0;require(!verify(),"normal ownership");}
        else if(name=="bad_shape") {s.faces[0].ring.pop_back();require(!verify(),"complete oriented closed shape");}
        else if(name=="nonfinite") {s.points[0][0]=std::numeric_limits<double>::quiet_NaN();require(!verify(),"finite DP");}
        else if(name=="budget") {require(!verifyMany(s,o,p,0,no),"deadline");}
        else if(name=="budget_during_origins") {
            int checks=0;require(!verifyMany(s,o,p,0,[&](){return ++checks<3;}),"shared deadline stop");
        }
        else if(name=="origin_cap") {s=boxes(9);o=records(s);require(!verify(),"max origins");}
        else if(name=="lineage_full_union") {
            L l;require(l.initialize(5,[](int){return true;},yes),"init");
            for(int c=1;c<5;++c)require(l.join(0,c,yes),"union origin retention");
            std::vector<int> ids;require(l.originsForCells({0},ids)&&ids==std::vector<int>({0,1,2,3,4}),"complete5");
            require(!l.originsForCells({1},ids),"dropped unknown");require(l.compositeCount()==4,"bounded catalog");
        }
        else if(name=="lineage_deep_copy") {
            L l;require(l.initialize(2,[](int){return true;},yes),"init");auto saved=l;
            require(l.join(0,1,yes),"accepted later union");
            std::vector<int> ids;require(saved.originsForCells({1},ids)&&ids==std::vector<int>{1},"earliest remains own copy");
        }
        else if(name=="lineage_unknown") {
            L l;require(l.initialize(2,[](int c){return c==0;},yes),"partial known origins");
            require(!l.join(0,1,yes),"unknown never guess");std::vector<int> ids;require(!l.originsForCells({0},ids),"fail closed");
        }
        else if(name=="lineage_repeat") {
            L l;require(l.initialize(2,[](int){return true;},yes),"init");
            require(!l.initialize(2,[](int){return true;},yes)&&!l.active(),"no later recapture");
        }
        else if(name=="lineage_cell_cap") {
            L l;require(!l.initialize(maxKeyBytes/sizeof(int)+1,[](int){return true;},yes),"cap before allocation");
            require(l.keyCapacityBytes()==0,"no huge allocation");
        }
        else if(name=="lineage_origin_cap") {
            L l;require(l.initialize(9,[](int){return true;},yes),"init");
            for(int c=1;c<8;++c)require(l.join(0,c,yes),"eight origins");
            require(!l.join(0,8,yes),"nine refused");std::vector<int> ids;require(!l.originsForCells({0},ids),"unknown after overflow");
        }
        else if(name=="lineage_sparse_cap") {
            L l;require(l.initialize(8194,[](int){return true;},yes),"init");
            for(int c=0;c<8192;c+=2)require(l.join(c,c+1,yes),"4096 bounded unique sets");
            require(!l.join(8192,8193,yes),"catalog overflow refused");
            require(l.compositeCount()==4096&&l.compositeCapacityBytes()<=maxCompositeBytes,"hard sparse capacity");
            require(l.refusals()[std::size_t(Refusal::compositeCapacity)]==1,"record reason");
        }
        else if(name=="lineage_budget") {
            L l;require(l.initialize(2,[](int){return true;},yes),"init");
            require(!l.join(0,1,no)&&!l.active(),"expired no later permission");
        }
        else if(name=="lineage_mapping") {
            L l;require(l.initialize(3,[](int){return true;},yes),"init");
            std::vector<int> compact;require(l.compactKeys({1,-1,0},2,compact)&&compact==std::vector<int>({2,0}),"real remap");
            require(!l.compactKeys({0,0,-1},1,compact),"duplicate binding");
            require(!l.compactKeys({0,-1,-1},2,compact),"missing binding");
        }
        else if(name=="lineage_cavity_cap") {
            L l;require(l.initialize(129,[](int){return true;},yes),"init");
            std::vector<int> cells(129),ids;for(int i=0;i<129;++i)cells[i]=i;
            require(!l.originsForCells(cells,ids),"core/source cells cap before loop");
            require(!l.originsForCells({0,0},ids),"duplicate cell input");
        }
        else if(name=="lineage_disabled") {
            L l;l.inherit(0,0,yes);require(!l.join(0,1,yes)&&l.size()==0,"no authority no allocation");
        }
        else throw std::runtime_error("unknown test");
        std::cout<<name<<" PASS\n";return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
