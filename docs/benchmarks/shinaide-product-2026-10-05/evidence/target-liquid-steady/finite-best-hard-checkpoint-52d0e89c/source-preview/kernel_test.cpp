
#define DCCART_FINITE_REPAIR_KERNEL_ONLY
#include "dcCartFinitePointRepair.H"
#include <iostream>
#include <cassert>
using namespace dcCartFiniteRepairKernel;
int main(){
 for(int scenario=0;scenario<8;++scenario){
  unsigned calls=0;
  auto eval=[&](const Row&q){++calls;Evaluation e;e.valid=true;e.objective=-q[0];e.constraints={1}; double x=q[0];
    e.hardDW=x<.0039 ? 5 : x<.009 ? 3 : 4;
    if(scenario==1)e.hardDW=-1;
    if(scenario==2)e.hardDW=std::numeric_limits<double>::quiet_NaN();
    if(scenario==3)e.hardDW=2.5;
    if(scenario==4)e.hardDW=5;
    if(scenario==5)e.constraints={-.01};
    if(scenario==6)e.hardDW=-2;
    return e;};
  auto k=propose(3,eval,[](){return true;},scenario==7 ? ObjectiveKind::Ratio : ObjectiveKind::DWeightCount);
  assert(k.requests==calls);
  if(scenario==0){
   assert(k.improved && k.bestHardImproved && k.bestHardDW==3);
   assert(k.parameters[0]>k.bestHardParameters[0]);
   assert(eval(k.parameters).objective<eval(k.bestHardParameters).objective);
   assert(eval(k.parameters).hardDW==4);
   // Full-guard illustration: primary four fail; saved four retain hard gain.
   unsigned primary=0,saved=0;
   for(double f:{1.,.95,.9,.5}) primary+=k.parameters[0]*f<.009;
   for(double f:{1.,.95,.9,.5}) saved+=k.bestHardParameters[0]*f<.009;
   assert(primary==0 && saved>0);
  }else assert(!k.bestHardImproved && k.bestHardParameters.empty());
  std::cout<<"scenario "<<scenario<<" PASS requests="<<k.requests<<"\n";
 }
 // Default/unset adds no requests or changes to the soft trajectory.
 auto ev=[](const Row&q){Evaluation e;e.valid=true;e.objective=-q[0];e.constraints={1};return e;};
 auto a=propose(3,ev,[](){return true;},ObjectiveKind::Ratio);
 auto b=propose(3,ev,[](){return true;},ObjectiveKind::DWeightCount);
 assert(a.parameters==b.parameters && a.requests==b.requests);
 assert(!a.bestHardImproved && !b.bestHardImproved);
 std::cout<<"unset-soft-identical PASS\n";
}
