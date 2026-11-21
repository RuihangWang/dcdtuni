#ifndef dcCartGeneratedAPSourceDelta_H
#define dcCartGeneratedAPSourceDelta_H
#include "dcCartRawUnionStockMetrics.H"
#include "dcCartOriginalNodePositiveEarFacets.H"
#include <cstdint>
#include <cstring>
#include <map>
#include <set>
#include <vector>

namespace Foam { namespace dcCartGeneratedAPSourceDelta {
namespace Q=dcCartRawUnionStockMetrics;
namespace I=dcCartOriginalNodePositiveEarFacets;
struct Move {label nativePoint=-1;point before=point::zero,target=point::zero;};
struct CellShell {label nativeCell=-1,recordedRoot=-1;std::vector<Q::Face> faces;};
struct Result {
 std::vector<label> rootIDs;
 std::size_t cellCount=0,internalFaces=0,skinFaces=0;
};
inline bool samePoint(const point& a,const point& b) {
 static_assert(sizeof(scalar)==sizeof(std::uint64_t),"binary64 required");
 for(label k=0;k<3;++k){std::uint64_t x,y;std::memcpy(&x,&a[k],sizeof x);std::memcpy(&y,&b[k],sizeof y);if(x!=y)return false;}return true;
}
inline bool sameFace(const Q::Face& a,const Q::Face& b) {
 return a.id==b.id&&a.owner==b.owner&&a.neighbour==b.neighbour
  &&a.patch==b.patch&&a.zone==b.zone&&a.ring==b.ring;
}
// Exact local change measurement only. The caller must separately bind complete
// actual global AP incidence, genuine derived birth/source floor, recorded roots,
// immutable source authority, processor peers, final Stock and fresh publication.
// This is neither a source permission token nor a Point admission substitute.
inline bool measure(const std::vector<point>& before,const std::vector<point>& shadow,
 const std::vector<Move>& moves,const std::vector<CellShell>& completeCurrentStar,
 const Q::Available& work,Result& out,const char** why=nullptr) {
 out=Result();const auto no=[&](const char* text){if(why)*why=text;out=Result();return false;};
 try {
  if(!work||!Q::workAvailable(work)||before.size()!=shadow.size()||moves.empty()||moves.size()>8
    ||completeCurrentStar.empty()||completeCurrentStar.size()>256)return no("ap-delta-input-or-clock");
  std::set<label> moving;bool anyChange=false;
  for(const auto& move:moves){if(!Q::workAvailable(work)||move.nativePoint<0||std::size_t(move.nativePoint)>=before.size()
    ||!moving.insert(move.nativePoint).second||!Q::finitePoint(move.before)||!Q::finitePoint(move.target)
    ||!samePoint(move.before,before[move.nativePoint])||!samePoint(move.target,shadow[move.nativePoint]))
     return no("ap-delta-move-binding");anyChange=anyChange||!samePoint(move.before,move.target);}
  if(!anyChange)return no("ap-delta-no-coordinate-change");
  std::map<label,label> roots;std::map<label,const CellShell*> cells;
  for(const auto& cell:completeCurrentStar){if(!Q::workAvailable(work)||cell.nativeCell<0||cell.recordedRoot<0
    ||cell.faces.size()<4||cell.faces.size()>512||!cells.emplace(cell.nativeCell,&cell).second)
     return no("ap-delta-cell-input");roots.emplace(cell.nativeCell,cell.recordedRoot);}
  struct Use {Q::Face face;std::set<label> cells;};
  std::map<label,Use> inventory;std::set<label> nodes,observedMoving;
  I::E::Context exact(work);std::map<label,I::Integrals> oldRoot,newRoot;
  auto oldAt=[&](label id)->const point&{return before.at(id);};
  auto newAt=[&](label id)->const point&{return shadow.at(id);};
  for(const auto& cell:completeCurrentStar){
   if(!Q::workAvailable(work))return no("ap-delta-clock");
   std::set<label> seenFaces;std::map<std::pair<label,label>,std::pair<unsigned,int>> edges;
   I::Integrals oldCell,newCell;
   for(const auto& face:cell.faces){
    if(!Q::workAvailable(work)||face.id<0||face.owner<0||face.neighbour<-1||face.owner==face.neighbour
      ||face.patch<-1||face.zone<-1||face.ring.size()<3||face.ring.size()>128
      ||(face.owner!=cell.nativeCell&&face.neighbour!=cell.nativeCell)||!seenFaces.insert(face.id).second)
       return no("ap-delta-complete-CF-face");
    std::set<label> unique;bool hasMoving=false;
    for(label id:face.ring){if(!Q::workAvailable(work)||id<0||std::size_t(id)>=before.size()
      ||!unique.insert(id).second||!Q::finitePoint(before[id])||!Q::finitePoint(shadow[id]))
       return no("ap-delta-face-point");nodes.insert(id);
     if(moving.count(id)){hasMoving=true;observedMoving.insert(id);}
     else if(!samePoint(before[id],shadow[id]))return no("ap-delta-unregistered-point-change");
    }
    if(nodes.size()>32768)return no("ap-delta-node-bound");
    if(hasMoving&&(face.patch!=-1||face.zone!=-1||face.neighbour<0||!roots.count(face.owner)
      ||!roots.count(face.neighbour)||roots.at(face.owner)!=roots.at(face.neighbour)))
       return no("ap-delta-moving-point-on-protected-or-star-skin");
    auto found=inventory.find(face.id);
    if(found==inventory.end()){if(inventory.size()>=4096)return no("ap-delta-face-bound");
     Use entry;entry.face=face;entry.cells.insert(cell.nativeCell);inventory.emplace(face.id,std::move(entry));}
    else if(!sameFace(found->second.face,face)||!found->second.cells.insert(cell.nativeCell).second)
     return no("ap-delta-shared-face-binding");
    auto ring=face.ring;if(face.owner!=cell.nativeCell)std::reverse(ring.begin()+1,ring.end());
    for(std::size_t j=0;j<ring.size();++j){if(!Q::workAvailable(work))return no("ap-delta-clock");
     const label a=ring[j],b=ring[(j+1)%ring.size()];auto& edge=edges[std::minmax(a,b)];++edge.first;edge.second+=a<b?1:-1;}
    I::add(oldCell,I::integrals(ring,oldAt,exact),exact);I::add(newCell,I::integrals(ring,newAt,exact),exact);
    if(!exact.good())return no("ap-delta-exact-resource-or-clock");
   }
   for(const auto& edge:edges)if(edge.second.first!=2||edge.second.second!=0)return no("ap-delta-open-or-inconsistent-cell");
   if(I::E::sign(oldCell.sixV)<=0||I::E::sign(newCell.sixV)<=0)return no("ap-delta-nonpositive-signed-cell-volume");
   I::add(oldRoot[cell.recordedRoot],oldCell,exact);I::add(newRoot[cell.recordedRoot],newCell,exact);
   if(!exact.good())return no("ap-delta-exact-resource-or-clock");
  }
  if(observedMoving!=moving)return no("ap-delta-missing-moving-point-incidence");
  Result measured;measured.cellCount=completeCurrentStar.size();
  for(const auto& row:inventory){if(!Q::workAvailable(work))return no("ap-delta-clock");
   const auto& face=row.second.face;std::set<label> expected;
   if(cells.count(face.owner))expected.insert(face.owner);
   if(face.neighbour>=0&&cells.count(face.neighbour))expected.insert(face.neighbour);
   if(row.second.cells!=expected)return no("ap-delta-incomplete-opposite-CF");
   const bool sameRoot=expected.size()==2&&roots.at(face.owner)==roots.at(face.neighbour);
   if(sameRoot)++measured.internalFaces;
   else{++measured.skinFaces;for(label id:face.ring)
    if(moving.count(id)||!samePoint(before[id],shadow[id]))return no("ap-delta-skin-point-bits");}
  }
  for(const auto& row:oldRoot){if(!Q::workAvailable(work)||!I::same(row.second,newRoot.at(row.first),exact))
    return no("ap-delta-root-exact-surface12-6V24M");measured.rootIDs.push_back(row.first);}
  if(!exact.good()||!Q::workAvailable(work))return no("ap-delta-exact-resource-or-clock");
  out=std::move(measured);return true;
 }catch(...){return no("ap-delta-unknown-or-allocation");}
}
} }
#endif
