#ifndef dcCartLiteralWeightProofCore_H
#define dcCartLiteralWeightProofCore_H
#include "point.H"
#include <vector>
#include <string>
#include <set>
#include <cstring>
#include <cmath>
namespace Foam { namespace dcCartLiteralWeightProof {
struct Alias {label original=-1,current=-1;point before,after;};
struct Facet {label native=-1,owner=-1,neighbour=-1,zone=-1;bool protectedRole=false;
 std::string physicalRole;std::vector<point> ring;std::vector<Alias> aliases;};
struct Shape {label native=-1;bool complete=false,changed=false;std::vector<Facet> faces;};
inline bool equal(const point&a,const point&b){for(unsigned i=0;i<3;++i)
 if(!std::isfinite(a[i])||!std::isfinite(b[i])||std::memcmp(&a[i],&b[i],sizeof(scalar)))return false;return true;}
inline bool shape(const Shape&a,const Shape&b){
 if(!a.complete||!b.complete||a.changed||b.changed||a.native!=b.native||a.faces.size()<4||a.faces.size()!=b.faces.size())return false;
 std::set<label> seen;
 for(const auto& f:a.faces){
  if(f.native<0||!seen.insert(f.native).second||f.protectedRole)return false;
  const Facet*g=nullptr;for(const auto& x:b.faces)if(x.native==f.native){if(g)return false;g=&x;}
  if(!g||g->protectedRole||f.owner!=g->owner||f.neighbour!=g->neighbour||f.zone!=g->zone
     ||f.physicalRole!=g->physicalRole||f.ring.size()!=g->ring.size()
     ||f.aliases.size()!=g->aliases.size()||f.aliases.size()!=f.ring.size())return false;
  for(size_t j=0;j<f.ring.size();++j){
   if(!equal(f.ring[j],g->ring[j]))return false;
   const auto& x=f.aliases[j];const auto& y=g->aliases[j];
   if(x.original<0||x.current!=x.original||y.original!=x.original||y.current!=x.original
      ||!equal(x.before,x.after)||!equal(x.before,y.before)||!equal(x.before,y.after))return false;
  }
 } return true;
}
inline bool credit(scalar before,scalar after,scalar threshold) {
 return std::isfinite(before)&&std::isfinite(after)&&std::isfinite(threshold)
    &&before>=0&&after>=0&&threshold>0&&before<threshold&&after<threshold;
}
inline bool proof(const Shape& a,const Shape& b,const Shape& c,const Shape& d)
 {return a.native!=b.native&&shape(a,c)&&shape(b,d);}
} }
#endif
