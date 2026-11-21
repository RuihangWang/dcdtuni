#include "dcCartCompleteCavityAuthority.H"
#include "fixture.H"
#include "IFstream.H"
#include <iostream>
using namespace Foam;
namespace O=dcCartPostCarveFluidOwnershipPreview;
namespace C=dcCartCompleteCavityAuthority;
dcCartCutCells::cutMesh meshFixture()
{
    const auto s=boxes();dcCartCutCells::cutMesh m;m.nCells=s.parents.size();
    for(const auto& x:s.points)m.points.push_back(point(x[0],x[1],x[2]));
    for(const auto& f:s.faces)
    {
        m.faces.emplace_back(f.ring.begin(),f.ring.end());m.owner.push_back(f.owner);
        m.neighbour.push_back(f.neighbour);m.patch.push_back(f.patch);m.zone.push_back(f.zone);
        m.faceAlive.push_back(true);
    }
    for(const auto& p:s.parents)
    {
        m.cellAlive.push_back(true);m.cellRank.push_back(0);m.cellLevel.push_back(std::get<2>(p.identity));
        m.cellLeaf.push_back(std::get<3>(p.identity));m.cellPiece.push_back(std::get<4>(p.identity));
    }
    return m;
}
int main(int argc,char** argv)
{
    try {
        require(argc==3,"dict and case");const std::string mode=argv[2];
        IFstream input(argv[1]);dictionary d(input);auto m=meshFixture();
        wordList names(1),types(1),zones;names[0]="wall";types[0]="wall";
        if(mode=="early_empty" || mode=="early_oversized")
        {
            dcCartCutCells::completeCavityAuthority a;
            a.recordedParent=[](label){return false;};
            a.normalOriginFromSnapshot=[](label){return dcCartCutCells::weightUnionOrigin();};
            a.completeOrigins=[](const std::vector<dcCartCutCells::weightUnionOrigin>&){return false;};
            a.remainingSeconds=[]()->scalar {throw std::runtime_error("clock must not be called before size-cap refusal");};
            std::vector<label> cells(mode=="early_empty"?0:129,0);
            std::vector<dcCartCutCells::weightUnionOrigin> out(1);
            require(!C::collectCompleteOrigins(m,a,cells,out)&&out.empty(),"early refusal without clock/mesh scan");
            std::cout<<mode<<" PASS actualOF early-size-cap; throwing clock not reached\n";return 0;
        }
        scalar remaining=10;std::function<scalar()> clock=[&](){return remaining;};
        if(mode=="nonfinite_budget")remaining=std::numeric_limits<scalar>::quiet_NaN();
        if(mode=="nonlocal")m.cellRank[3]=1;
        if(mode=="bad_shape")m.faces[0].pop_back();
        auto snapshot=O::capture(m,d,names,types,zones,Pstream::myProcNo(),mode!="alternate_path",clock);
        auto pair=dcCartWeightUnionSourceContract::readAuthority
            (d,O::verifier(snapshot),clock,[snapshot](label p){return O::origin(snapshot,p);});
        if(mode=="global_bool_only")
        {
            dcCartCutCells::weightUnionAuthority impostor;impostor.globallyNoMaterialNoBulk=true;
            require(!C::read(d,snapshot,impostor,clock).ready(),"bool is not authority");
        }
        else
        {
            if(mode=="stale_boundary")
                d.subDict("dcdtuniWeightUnionSourceContract").set("boundaryMembershipDigest",Foam::string(std::string(64,'f')));
            auto authority=C::read(d,snapshot,pair,clock);
            if(mode=="false_contract" || mode=="alternate_path" || mode=="nonfinite_budget" || mode=="stale_boundary")
                require(!authority.ready(),"default false/stale/unknown path");
            else
            {
                require(authority.ready(),"real parsed fresh normal contract");
                std::vector<dcCartCutCells::weightUnionOrigin> origins;
                for(label p=0;p<5;++p)origins.push_back(O::origin(snapshot,p));
                if(mode=="bad_shape" || mode=="nonlocal")require(!authority.completeOrigins(origins),"unknown origin/shape");
                else
                {
                    if(mode=="legacy_pair")
                    {
                        require(pair.homogeneousOrigins({origins[0],origins[1]}),"old pair keeps positive");
                        require(!pair.homogeneousOrigins(origins),"old pair cannot become five");
                        require(!pair.homogeneousOrigins({origins[0],origins[0]}),"old distinct-origin rule");
                    }
                    else if(mode=="duplicate") {origins[4]=origins[0];require(!authority.completeOrigins(origins),"duplicates refused");}
                    else if(mode=="facet_missing") {origins[0].sourceFacets.pop_back();require(!authority.completeOrigins(origins),"complete facets");}
                    else if(mode=="wrong_region") {origins[0].region="other";require(!authority.completeOrigins(origins),"unknown IO path");}
                    else
                    {
                        require(authority.completeOrigins(origins),"five real normal origins");
                        auto available=[&](){return C::available(clock);};
                        require(m.completeCavityLineage.initialize(5,authority.recordedParent,available),"normal initialize");
                        if(mode=="current_live_shape_handles")
                        {
                            std::vector<dcCartCutCells::weightUnionOrigin> collected;
                            require(C::collectCompleteOrigins(m,authority,{0,1,2,3,4},collected)&&collected.size()==5,"live complete shape lineage");
                            m.cellAlive[3]=false;
                            require(!C::collectCompleteOrigins(m,authority,{0,1,2,3,4},collected)&&collected.empty(),"dead slot refuses");
                        }
                        else if(mode=="clone_and_append")
                        {
                            auto earliest=m;
                            m.nCells=6;m.cellAlive.push_back(true);
                            m.completeCavityLineage.inherit(5,0,available);
                            require(m.completeCavityLineage.join(0,5,available),"same-origin cone union");
                            m.cellAlive[5]=false;
                            std::vector<dcCartCutCells::weightUnionOrigin> collected;
                            require(C::collectCompleteOrigins(m,authority,{0,1,2,3,4},collected)&&collected.size()==5,"full ancestry");
                            require(earliest.completeCavityLineage.size()==5&&earliest.cellAlive.size()==5,"earliest deep copy");
                        }
                        else if(mode=="expires")
                        {
                            remaining=0;std::vector<dcCartCutCells::weightUnionOrigin> collected;
                            require(!C::collectCompleteOrigins(m,authority,{0,1,2,3,4},collected),"real dynamic deadline");
                        }
                        else if(mode=="immutable_snapshot")
                        {
                            m.points[0]=point(100,100,100);m.faces[0].clear();
                            require(snapshot->points[0][0]==0&&snapshot->faces[0].ring.size()==4,"owned immutable producer");
                            require(authority.completeOrigins(origins),"source permission only; does not authorize new geometry");
                        }
                    }
                }
            }
        }
        std::cout<<mode<<" PASS actualOF labelBytes="<<sizeof(label)<<" scalarBytes="<<sizeof(scalar)<<"\n";return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
