#include "point.H"
#include "vector.H"
#include <cmath>
#include <cstring>
#include <cstdint>
#include <vector>
#include <iostream>
#include <iomanip>
#include <cfenv>
#include <sys/wait.h>
#include <sys/resource.h>
#include <unistd.h>
using namespace Foam;
static_assert(sizeof(scalar)==8 && sizeof(label)==4,"Require actual OpenFOAM DPInt32");
scalar D(std::uint64_t x){scalar s;std::memcpy(&s,&x,8);return s;}
point P(std::uint64_t x,std::uint64_t y,std::uint64_t z){return point(D(x),D(y),D(z));}
std::uint64_t bits(scalar x){std::uint64_t n;std::memcpy(&n,&x,8);return n;}
bool same(const point& a,const point& b){return bits(a[0])==bits(b[0]) && bits(a[1])==bits(b[1]) && bits(a[2])==bits(b[2]);}
void emit(const point& p){std::cout<<'[';for(int k=0;k<3;++k){if(k)std::cout<<',';std::cout<<'"'<<std::hex<<std::setw(16)<<std::setfill('0')<<bits(p[k])<<'"';}std::cout<<std::dec<<']';}
inline void faceAreaCentreOF
(
    const std::vector<Foam::point>& pts,
    const std::vector<Foam::label>& poly,
    Foam::vector& A,
    Foam::point& C
)
{
    using namespace Foam;
    A = vector::zero;
    C = point::zero;
    const label k = label(poly.size());
    if (k < 3)
    {
        for (label v : poly) { C += pts[v]; }
        if (k) { C /= scalar(k); }
        return;
    }
    if (k == 3)
    {
        C = (pts[poly[0]] + pts[poly[1]] + pts[poly[2]]) / 3.0;
        A = 0.5 * ((pts[poly[1]] - pts[poly[0]]) ^ (pts[poly[2]] - pts[poly[0]]));
        return;
    }
    point fc = point::zero;
    for (label i = 0; i < k; ++i) { fc += pts[poly[i]]; }
    fc /= scalar(k);
    vector sumN = vector::zero;
    vector sumAc = vector::zero;
    scalar sumA = 0;
    for (label i = 0; i < k; ++i)
    {
        const point& p = pts[poly[i]];
        const point& q = pts[poly[(i + 1) % k]];
        const vector n = (q - p) ^ (fc - p);
        const scalar a = mag(n);
        sumN += n;
        sumA += a;
        sumAc += a * (p + q + fc);
    }
    if (sumA < ROOTVSMALL)
    {
        C = fc;
        return;
    }
    // Match primitiveMeshTools operation order, including rounding at the
    // strict skewness limit. Algebraically equivalent expressions can fall
    // on opposite sides of checkMesh's boundary threshold.
    C = (1.0 / 3.0) * sumAc / sumA;
    A = 0.5 * sumN;
}


point restore(const std::vector<point>& points,const std::vector<label>& poly,const vector& av,const point& fc){
    struct Source {const std::vector<point>& points;} m{points};
                // New fan points must stay on a bit-literal axis support plane.
                // Keep the original numeric centroid for metrics/conservation;
                // only the coordinate passed to insertPoint is restored.
                point fanCentre=fc;
                const scalar fanAreaSqr=magSqr(av);
                bool finiteFan=poly.size()>=3 && std::isfinite(fanAreaSqr)
                    && fanAreaSqr>0;
                for (direction k=0;k<3;++k)
                    finiteFan=finiteFan && std::isfinite(fc[k]);
                for (label v:poly) for (direction k=0;k<3;++k)
                    finiteFan=finiteFan && std::isfinite(m.points[v][k]);
                if (finiteFan)
                {
                    direction supportAxis=0;label fixedAxes=0;
                    for (direction k=0;k<3;++k)
                    {
                        const scalar plane=m.points[poly[0]][k];bool fixed=true;
                        for (label v:poly)
                        {
                            const scalar value=m.points[v][k];
                            if (std::memcmp(&plane,&value,sizeof(scalar))!=0)
                            { fixed=false;break; }
                        }
                        if (fixed) { supportAxis=k;++fixedAxes; }
                    }
                    // A degenerate line/point or non-axis plane is not this grammar.
                    // Signed-zero mixtures are not a bit-literal fixed component.
                    if (fixedAxes==1)
                        fanCentre[supportAxis]=m.points[poly[0]][supportAxis];
                }

    return fanCentre;
}
struct Control{const char* name;std::vector<point> vertices;point fc;vector av;point expected;};
struct Authentic{label source;std::vector<point> vertices;std::vector<label> canonical;point expectedFc;vector expectedAv;};
int main(){
 struct rlimit core{0,0};setrlimit(RLIMIT_CORE,&core);
 std::vector<Control> controls={{"actual-source-3579", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb333340000000ULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"actual-source-3526", {P(0x3ff0000000000000ULL,0x3ff0000000000000ULL,0x3fee666660000000ULL),P(0x3ff0000000000000ULL,0x3ff4000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3ff4000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3fee666660000000ULL),P(0x3ff0000000000000ULL,0x3fecccccc0000000ULL,0x3fee666660000000ULL)}, P(0x3fed9999a0000000ULL,0x3ff1333330000001ULL,0x3fee66665ffffffeULL), P(0x0000000000000000ULL,0x0000000000000000ULL,0xbfaae147851eb800ULL), P(0x3fed9999a0000000ULL,0x3ff1333330000001ULL,0x3fee666660000000ULL)},{"one-source-vertex-shifted-nonconstant", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000001ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"tilted-no-axis-support", {P(0x3ff07ae14ccccccdULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3ff0ccccd3333333ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3ff0ccccd3333333ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3ff0ccccd3333333ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3ff07ae14ccccccdULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3ff07ae14ccccccdULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"finite-non-axis-triangle", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x3ff0000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL)}, P(0x3fd3333333333333ULL,0x3fd3333333333333ULL,0x3fe3333333333333ULL), P(0x3ff0000000000000ULL,0x3ff0000000000000ULL,0xbff0000000000000ULL), P(0x3fd3333333333333ULL,0x3fd3333333333333ULL,0x3fe3333333333333ULL)},{"NaN-original-vertex", {P(0x7ff8000000000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"Inf-original-vertex", {P(0x3feb333340000000ULL,0x7ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"nonfinite-original-centre", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x7ff8000000000000ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x7ff8000000000000ULL,0x3ff666665fffffffULL)},{"nonfinite-area", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0x7ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"overflow-area-square", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0x7fe1ccf385ebc8a0ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"degenerate-line", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL),P(0x0000000000000000ULL,0x4000000000000000ULL,0x4000000000000000ULL)}, P(0x3fb999999999999aULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL), P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3fb999999999999aULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL)},{"degenerate-repeated-point", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL)}, P(0x3fb999999999999aULL,0x3fb999999999999aULL,0x3fb999999999999aULL), P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3fb999999999999aULL,0x3fb999999999999aULL,0x3fb999999999999aULL)},{"tiny-area-square-underflow", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0x16687e92154ef7acULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"mixed-signed-zero-support-not-bit-literal", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x8000000000000000ULL,0x3ff0000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL),P(0x0000000000000000ULL,0x0000000000000000ULL,0x3ff0000000000000ULL)}, P(0x3fb999999999999aULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL), P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3fb999999999999aULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL)},{"uniform-positive-zero-support", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL),P(0x0000000000000000ULL,0x0000000000000000ULL,0x3ff0000000000000ULL)}, P(0x8000000000000000ULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL), P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x0000000000000000ULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL)},{"uniform-negative-zero-support", {P(0x8000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x8000000000000000ULL,0x3ff0000000000000ULL,0x0000000000000000ULL),P(0x8000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL),P(0x8000000000000000ULL,0x0000000000000000ULL,0x3ff0000000000000ULL)}, P(0x0000000000000000ULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL), P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x8000000000000000ULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL)},{"fewer-than-three-source-vertices", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL)}, P(0x3fb999999999999aULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL), P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3fb999999999999aULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL)}};std::vector<Authentic> actual={{3579, {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, {0,5,4,3,2,1}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL)},{3526, {P(0x3ff0000000000000ULL,0x3ff0000000000000ULL,0x3fee666660000000ULL),P(0x3ff0000000000000ULL,0x3ff4000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3ff4000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3fee666660000000ULL),P(0x3ff0000000000000ULL,0x3fecccccc0000000ULL,0x3fee666660000000ULL)}, {4,3,2,1,0,5}, P(0x3fed9999a0000000ULL,0x3ff1333330000001ULL,0x3fee66665ffffffeULL), P(0x0000000000000000ULL,0x0000000000000000ULL,0xbfaae147851eb800ULL)}};bool good=true;
 for(const auto& c:controls){std::vector<label> poly;for(label i=0;i<label(c.vertices.size());++i)poly.push_back(i);const vector beforeA=c.av;const point beforeC=c.fc;
  feclearexcept(FE_ALL_EXCEPT);const point out=restore(c.vertices,poly,c.av,c.fc);const int exceptions=fetestexcept(FE_ALL_EXCEPT);const bool pass=same(out,c.expected)&&same(c.av,beforeA)&&same(c.fc,beforeC);good=good&&pass;
  std::cout<<"{\"kind\":\"local-property\",\"name\":\""<<c.name<<"\",\"pass\":"<<(pass?"true":"false")<<",\"exceptions\":"<<exceptions<<",\"afterBits\":";emit(out);std::cout<<"}\n";
 }
 for(const auto& c:actual){vector av;point fc;faceAreaCentreOF(c.vertices,c.canonical,av,fc);const vector saved=av;const point savedFc=fc;const point out=restore(c.vertices,c.canonical,av,fc);bool tangential=true;int axes=0;
  for(direction k=0;k<3;++k){bool fixed=true;for(const point& v:c.vertices)fixed=fixed&&(bits(v[k])==bits(c.vertices[0][k]));if(fixed){++axes;tangential=tangential&&bits(out[k])==bits(c.vertices[0][k]);}else tangential=tangential&&bits(out[k])==bits(fc[k]);}
  const bool pass=same(fc,c.expectedFc)&&same(av,c.expectedAv)&&same(av,saved)&&same(fc,savedFc)&&axes==1&&tangential;good=good&&pass;
  std::cout<<"{\"kind\":\"authentic-helper\",\"source\":"<<c.source<<",\"pass\":"<<(pass?"true":"false")<<",\"fcBits\":";emit(fc);std::cout<<",\"avBits\":";emit(av);std::cout<<",\"fanBits\":";emit(out);std::cout<<"}\n";
 }
 // Direct isolated magnitude exception probes, not full mesher reachability.
 for(scalar x:{scalar(1e308),scalar(1e-200),scalar(0),scalar(1)}){
  const vector av(x,0,0);feclearexcept(FE_ALL_EXCEPT);volatile scalar old=mag(av);int oldFlags=fetestexcept(FE_ALL_EXCEPT);feclearexcept(FE_ALL_EXCEPT);volatile scalar added=magSqr(av);int newFlags=fetestexcept(FE_ALL_EXCEPT);
  const bool sameClassification=(oldFlags&(FE_INVALID|FE_DIVBYZERO|FE_OVERFLOW|FE_UNDERFLOW))==(newFlags&(FE_INVALID|FE_DIVBYZERO|FE_OVERFLOW|FE_UNDERFLOW));good=good&&sameClassification;
  std::cout<<"{\"kind\":\"magnitude-flags\",\"value\":"<<std::setprecision(17)<<x<<",\"oldFlags\":"<<oldFlags<<",\"newFlags\":"<<newFlags<<",\"sameClassification\":"<<(sameClassification?"true":"false")<<"}\n";
  for(int mode=0;mode<2;++mode){pid_t pid=fork();if(pid==0){feclearexcept(FE_ALL_EXCEPT);feenableexcept(FE_INVALID|FE_DIVBYZERO|FE_OVERFLOW|FE_UNDERFLOW);volatile scalar input=x;const vector a(scalar(input),0,0);volatile scalar result=mode?magSqr(a):mag(a);(void)result;_exit(0);}int status=0;waitpid(pid,&status,0);std::cout<<"{\"kind\":\"isolated-trap\",\"value\":"<<x<<",\"operation\":\""<<(mode?"new-magSqr":"old-mag")<<"\",\"signal\":"<<(WIFSIGNALED(status)?WTERMSIG(status):0)<<",\"exit\":"<<(WIFEXITED(status)?WEXITSTATUS(status):-1)<<"}\n";}
 }
 std::cout<<"{\"kind\":\"summary\",\"localProperties\":"<<controls.size()<<",\"authenticHelperCases\":"<<actual.size()<<",\"pass\":"<<(good?"true":"false")<<"}\n";return good?0:1;
}
