#ifndef dcCartStableLocalFaceCentre_H
#define dcCartStableLocalFaceCentre_H
#include <array>
#include <cmath>
#include <limits>
#include <vector>
namespace dcCartStableLocalFaceCentre
{
// Same centre-fan area/area-weighted-centre equations as faceAreaCentreOF.
// Coordinates are read only; arithmetic uses one deterministic local origin.
// This numeric helper is not an OpenFOAM installed quality/geometry waiver.
template<class Points,class Ring,class Vector,class Point>
bool calculate(const Points& pts,const Ring& poly,Vector& A,Point& C,const double rootVerySmall)
{
 using L=long double;using V=std::array<L,3>;
 A=Vector::zero;C=Point::zero;
 const std::size_t k=poly.size();if(!k)return true;
 std::size_t anchor=0;
 for(std::size_t i=0;i<k;++i)
 {
  const auto id=poly[i];if(id<0||std::size_t(id)>=pts.size())return false;
  const auto&p=pts[id];
  for(unsigned d=0;d<3;++d)if(!std::isfinite(p[d]))return false;
  const auto&q=pts[poly[anchor]];
  if(p[0]<q[0]||(p[0]==q[0]&&(p[1]<q[1]||(p[1]==q[1]&&p[2]<q[2]))))anchor=i;
 }
 V origin{};for(unsigned d=0;d<3;++d)origin[d]=L(pts[poly[anchor]][d]);
 auto local=[&](std::size_t i){V p{};for(unsigned d=0;d<3;++d)p[d]=L(pts[poly[i]][d])-origin[d];return p;};
 auto cross=[](const V&a,const V&b){return V{{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}};};
 V fc{},sumN{},sumAc{};
 for(std::size_t i=0;i<k;++i){V p=local(i);for(unsigned d=0;d<3;++d)fc[d]+=p[d];}
 for(unsigned d=0;d<3;++d)fc[d]/=L(k);
 if(k<3){for(unsigned d=0;d<3;++d)C[d]=double(origin[d]+fc[d]);return true;}
 if(k==3)
 {
  V p=local(0),a=local(1),b=local(2);
  for(unsigned d=0;d<3;++d){a[d]-=p[d];b[d]-=p[d];}
  sumN=cross(a,b);
  for(unsigned d=0;d<3;++d){A[d]=double(.5L*sumN[d]);C[d]=double(origin[d]+fc[d]);}
 }
 else
 {
  L sumA=0;
  for(std::size_t i=0;i<k;++i)
  {
   V p=local(i),q=local((i+1)%k),edge{},radial{};
   for(unsigned d=0;d<3;++d){edge[d]=q[d]-p[d];radial[d]=fc[d]-p[d];}
   V n=cross(edge,radial);L area=std::sqrt(n[0]*n[0]+n[1]*n[1]+n[2]*n[2]);
   if(!std::isfinite(area))return false;
   sumA+=area;
   for(unsigned d=0;d<3;++d){sumN[d]+=n[d];sumAc[d]+=area*(p[d]+q[d]+fc[d]);}
  }
  if(sumA<L(rootVerySmall)){for(unsigned d=0;d<3;++d)C[d]=double(origin[d]+fc[d]);return true;}
  for(unsigned d=0;d<3;++d){A[d]=double(.5L*sumN[d]);C[d]=double(origin[d]+sumAc[d]/(3.L*sumA));}
 }
 for(unsigned d=0;d<3;++d)if(!std::isfinite(A[d])||!std::isfinite(C[d]))return false;
 return true;
}
}
#endif
