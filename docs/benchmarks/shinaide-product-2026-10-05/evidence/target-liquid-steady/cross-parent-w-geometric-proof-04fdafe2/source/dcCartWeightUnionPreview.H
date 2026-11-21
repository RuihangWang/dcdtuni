// ISOLATED PREVIEW: not imported, compiled, or authorized for a business run.
#ifndef dcCartWeightUnionPreview_H
#define dcCartWeightUnionPreview_H
#include "dcCartCutCells.H"
#include "dcCartLiteralWeightProofCore.H"
#include "polyMesh.H"
#include "cellZone.H"
#include "pointZone.H"
#include "faceZone.H"
#include "tetPointRef.H"
#include "symmTensor.H"
#include "mathematicalConstants.H"
#include "clockTime.H"
#include "OStringStream.H"
#include "IStringStream.H"
#include <array>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <map>
#include <limits>
#include <set>
#include <string>
#include <tuple>
#include <vector>

namespace Foam {
namespace dcCartWeightUnionPreview {
using Cut=dcCartCutCells::cutMesh;
using Bindings=dcCartCutCells::writerBindings;
using Origin=dcCartCutCells::weightUnionOrigin;
using Authority=dcCartCutCells::weightUnionAuthority;
using Key=std::vector<std::tuple<scalar,scalar,scalar>>;
inline bool finite(scalar x) { return std::isfinite(x); }
inline bool finite(const vector& p) { return finite(p.x())&&finite(p.y())&&finite(p.z()); }
inline bool bits(scalar a,scalar b)
{ return finite(a)&&finite(b)&&std::memcmp(&a,&b,sizeof(scalar))==0; }
inline bool bits(const vector& a,const vector& b)
{ return bits(a.x(),b.x())&&bits(a.y(),b.y())&&bits(a.z(),b.z()); }
inline bool sha(const std::string& v) {
    if(v.size()!=64) return false;
    for(char c:v) if(!((c>='0'&&c<='9')||(c>='a'&&c<='f'))) return false;
    return true;
}
inline std::string patchRole(const polyPatch& p) {
    OStringStream os;os.precision(std::numeric_limits<scalar>::max_digits10);p.write(os);
    IStringStream is(os.str());dictionary role(is);role.remove("startFace");
    OStringStream out;out.precision(std::numeric_limits<scalar>::max_digits10);
    role.writeEntries(out);return out.str();
}
inline bool zonedCell(const polyMesh& mesh,label c) {
    for(const auto& zone:mesh.cellZones()) if(zone.whichCell(c)>=0) return true;
    return false;
}
inline bool zonedFace(const polyMesh& mesh,label f) {
    for(const auto& zone:mesh.faceZones()) if(zone.whichFace(f)>=0) return true;
    return false;
}
inline bool zonedPoint(const polyMesh& mesh,label p) {
    for(const auto& zone:mesh.pointZones()) if(zone.whichPoint(p)>=0) return true;
    return false;
}
inline bool coupled(const polyMesh& mesh,label f) {
    return f>=mesh.nInternalFaces()
        && mesh.boundaryMesh()[mesh.boundaryMesh().patchID(f)].coupled();
}
inline scalar determinant(const polyMesh& mesh,label c) {
    scalar area=0;label count=0;
    for(label f:mesh.cells()[c]) if(f<mesh.nInternalFaces()||coupled(mesh,f))
    { area+=mag(mesh.faceAreas()[f]);++count; }
    if(!count||!finite(area)||area<=VSMALL) return -GREAT;
    const scalar mean=area/count;symmTensor t(Zero);
    for(label f:mesh.cells()[c]) if(f<mesh.nInternalFaces()||coupled(mesh,f))
        t+=sqr(mesh.faceAreas()[f]/mean);
    return mag(det(t))/8;
}
inline std::array<scalar,4> metrics(const polyMesh& mesh,label f) {
    const vector sf=mesh.faceAreas()[f];const point fc=mesh.faceCentres()[f];
    const auto& cc=mesh.cellCentres();const auto& vol=mesh.cellVolumes();
    const label o=mesh.faceOwner()[f];const scalar a=mag(sf);
    if(!finite(sf)||!finite(fc)||!finite(cc[o])||!finite(vol[o])
       ||a<=1e-12||vol[o]<=VSMALL||coupled(mesh,f))
        return {{-GREAT,-GREAT,GREAT,GREAT}};
    std::array<scalar,4> q{{1,1,0,0}};vector delta;
    if(f<mesh.nInternalFaces()) {
        const label n=mesh.faceNeighbour()[f];
        if(!finite(cc[n])||!finite(vol[n])||vol[n]<=VSMALL)
            return {{-GREAT,-GREAT,GREAT,GREAT}};
        const scalar x=mag(sf&(fc-cc[o])),y=mag(sf&(cc[n]-fc));
        q[0]=min(x,y)/(x+y+VSMALL);
        q[1]=min(vol[o],vol[n])/(max(vol[o],vol[n])+VSMALL);
        delta=cc[n]-cc[o];
        q[3]=std::acos(max(scalar(-1),min(scalar(1),
          (sf&delta)/(a*mag(delta)+ROOTVSMALL))))*180/constant::mathematical::pi;
    } else { const vector n=sf/a;delta=n*(n&(fc-cc[o])); }
    const vector cpf=fc-cc[o],sv=cpf-((sf&cpf)/((sf&delta)+ROOTVSMALL))*delta;
    const vector dir=sv/(mag(sv)+ROOTVSMALL);
    scalar reach=(f<mesh.nInternalFaces()? .2:.4)*mag(delta)+ROOTVSMALL;
    for(label p:mesh.faces()[f]) reach=max(reach,mag(dir&(mesh.points()[p]-fc)));
    q[2]=mag(sv)/reach;
    return q;
}
inline bool pass(scalar x,unsigned k) {
    if(!finite(x)) return false;
    static const scalar t[5]={.001,.05,.01,4,70};
    return k<3?x>=t[k]:x<=t[k];
}
inline scalar severity(scalar x,unsigned k) {
    if(!finite(x)) return GREAT;
    static const scalar t[5]={.001,.05,.01,4,70};
    return max(scalar(0),k<3?t[k]/max(x,VSMALL)-1:x/t[k]-1);
}
struct Stats {
    std::array<label,5> bad{{0,0,0,0,0}};
    std::array<scalar,5> worst{{0,0,0,0,0}};
    void add(scalar v,unsigned k) {bad[k]+=!pass(v,k);worst[k]=max(worst[k],severity(v,k));}
};
struct RawFace {
    label id=-1,owner=-1,neighbour=-1,patch=-1,zone=-1;
    label installedOwner=-1,installedNeighbour=-1;
    std::vector<label> ring;
    std::vector<point> rawCoordinates,installedCoordinates;
    std::string physicalPatchRole;
    std::array<scalar,4> quality;
};
struct RawCell {
    label id=-1;
    std::vector<label> rawFaces;
    scalar d=0,volume=0;point centre;
};
struct Packet {
    label nativeFace=-1,a=-1,b=-1,keep=-1,drop=-1;
    scalar weight=1;
    Key key;
    std::array<Key,2> keeperKeys;
    std::vector<Origin> origins;
    std::vector<RawFace> faces;
    std::vector<RawCell> cells;
    std::map<label,std::pair<label,point>> aliases;
    std::vector<label> outer;
    std::size_t payload=0;
};
struct State {
    bool enabled=false,poolRefused=false;
    label examined=0,tried=0,accepted=0,dependencyRefused=0,guardRefused=0;
    label protectedRefused=0,geometryRefused=0,qualityRefused=0;
    std::vector<Packet> pool,committed;
    std::map<label,std::vector<Origin>> survivorOrigins;
    // Exact known cardinality remaps from the EXISTING authorized physical path.
    std::map<label,label> authorizedPhysicalPruned;
    std::map<label,scalar> installedPrefixCells;
    std::map<label,std::array<scalar,4>> installedPrefixFaces;
    Authority authority;
    clockTime clock;
    std::size_t payload=0;
    bool available() const {
        if(!enabled||!authority.remainingSeconds) return false;
        const scalar r=authority.remainingSeconds();
        return finite(r)&&r>1 && clock.elapsedTime()<4; // reserve 1 s local admission
    }
};
inline Key key(const polyMesh& mesh,label c) {
    std::set<std::tuple<scalar,scalar,scalar>> points;
    for(label f:mesh.cells()[c]) for(label p:mesh.faces()[f]) {
        const point& x=mesh.points()[p];
        if(!finite(x)) return {};
        points.emplace(x.x(),x.y(),x.z());
    }
    return Key(points.begin(),points.end());
}
inline bool tet(const polyMesh& mesh,label c) {
    if(c<0||c>=mesh.nCells()||mesh.cells()[c].size()!=4) return false;
    const Key k=key(mesh,c);
    if(k.size()!=4) return false;
    for(label f:mesh.cells()[c]) if(mesh.faces()[f].size()!=3) return false;
    return true;
}
inline bool inverse(const std::vector<label>& mapping,label size,std::vector<label>& out) {
    out.assign(size,-1);
    for(label i=0;i<label(mapping.size());++i) if(mapping[i]>=0) {
        if(mapping[i]>=size||out[mapping[i]]>=0) return false;
        out[mapping[i]]=i;
    }
    return std::find(out.begin(),out.end(),label(-1))==out.end();
}
// Every stored original facet/coordinate is immutable. No whole-field references survive.
inline void capture
(
    State& state,const Cut& raw,const polyMesh& oracle,const Bindings& map,
    const std::vector<label>& parent,const std::function<Origin(label)>& origin
) {
    if(!state.available()) return;
    std::vector<label> nativeCells,nativeFaces;
    if(!inverse(map.cell,oracle.nCells(),nativeCells)
      ||!inverse(map.face,oracle.nFaces(),nativeFaces)
      ||map.alias.size()!=raw.points.size()) {state.poolRefused=true;return;}
    const auto& cf=oracle.cells(); // once; owned geometry is already forced by caller
    struct Seed {scalar w;Key k;label face;};
    std::vector<Seed> seeds;
    for(label f=0;f<oracle.nInternalFaces();++f) {
        if((f&1023)==0&&!state.available()) {state.poolRefused=true;return;}
        const label a=oracle.faceOwner()[f],b=oracle.faceNeighbour()[f];
        const vector sf=oracle.faceAreas()[f];const point fc=oracle.faceCentres()[f];
        const scalar da=mag(sf&(fc-oracle.cellCentres()[a]));
        const scalar db=mag(sf&(oracle.cellCentres()[b]-fc));
        const scalar w=min(da,db)/(da+db+VSMALL);
        if(!finite(w)||w>=.05||w<0) continue;
        if(!tet(oracle,a)||!tet(oracle,b)||zonedFace(oracle,f)) continue;
        const auto q=metrics(oracle,f);
        if(!finite(q[0])||q[0]>=.05||q[0]<0) continue;
        const label na=nativeCells[a],nb=nativeCells[b];
        if(na>=label(parent.size())||nb>=label(parent.size())
           ||parent[na]<0||parent[nb]<0||parent[na]==parent[nb]) continue;
        Key k=key(oracle,a),other=key(oracle,b);k.insert(k.end(),other.begin(),other.end());
        std::sort(k.begin(),k.end());
        seeds.push_back({q[0],std::move(k),f});
        if(seeds.size()>4096) {state.poolRefused=true;return;} // refuse whole pool
    }
    std::map<Key,unsigned> multiplicity;
    for(const auto& s:seeds) ++multiplicity[s.k];
    std::sort(seeds.begin(),seeds.end(),[](const Seed& a,const Seed& b){
        return a.w!=b.w?a.w<b.w:a.k<b.k;
    });
    for(const auto& seed:seeds) {
        if(!state.available()) break;
        if(state.pool.size()>=256) break;
        if(multiplicity[seed.k]!=1) continue;
        const label f=seed.face,a=oracle.faceOwner()[f],b=oracle.faceNeighbour()[f];
        Packet p;p.nativeFace=nativeFaces[f];p.a=nativeCells[a];p.b=nativeCells[b];
        p.key=seed.k;p.weight=seed.w;p.keeperKeys={{key(oracle,a),key(oracle,b)}};
        if(p.keeperKeys[0]==p.keeperKeys[1]) continue;
        bool valid=true;
        for(label c:{p.a,p.b}) {
            if(c<0||c>=label(parent.size())||parent[c]<0||!origin) {valid=false;break;}
            const Origin source=origin(parent[c]);
            if(source.nativeParent!=parent[c]||source.rank<0||source.region.empty()) {valid=false;break;}
            p.origins.push_back(source);
        }
        if(!valid||p.origins.size()!=2||!state.authority.homogeneousOrigins(p.origins)) continue;
        std::set<label> first{a,b},all,faceSet,pointSet;
        for(label c:{a,b}) for(label x:cf[c]) {
            if(x!=f) p.outer.push_back(nativeFaces[x]);
            if(x<oracle.nInternalFaces()) {
                first.insert(oracle.faceOwner()[x]);first.insert(oracle.faceNeighbour()[x]);
            }
        }
        if(p.outer.size()!=6) continue;
        all=first;
        for(label c:first) for(label x:cf[c]) if(x<oracle.nInternalFaces()) {
            all.insert(oracle.faceOwner()[x]);all.insert(oracle.faceNeighbour()[x]);
        }
        if(all.size()>128) continue; // complete first+second, never truncated
        for(label c:all) {
            if(zonedCell(oracle,c)) {valid=false;break;}
            for(label x:cf[c]) faceSet.insert(x);
        }
        if(!valid||faceSet.size()>512) continue;
        for(label x:faceSet) {
            if(coupled(oracle,x)||zonedFace(oracle,x)) {valid=false;break;}
            for(label y:oracle.faces()[x]) {pointSet.insert(y);if(zonedPoint(oracle,y)) valid=false;}
        }
        if(!valid||pointSet.size()>1024) continue;
        for(label c:all) {
            RawCell row;row.id=nativeCells[c];row.d=determinant(oracle,c);
            row.centre=oracle.cellCentres()[c];row.volume=oracle.cellVolumes()[c];
            if(!finite(row.d)||!finite(row.centre)||!finite(row.volume)||row.volume<=VSMALL) {valid=false;break;}
            // For dependency equality use the complete RAW native face membership.
            for(label x:cf[c]) row.rawFaces.push_back(nativeFaces[x]);
            std::sort(row.rawFaces.begin(),row.rawFaces.end());
            p.cells.push_back(std::move(row));
        }
        for(label x:faceSet) {
            const label n=nativeFaces[x];
            RawFace row;row.id=n;row.owner=raw.owner[n];row.neighbour=raw.neighbour[n];
            row.patch=raw.patch[n];row.zone=raw.zone[n];row.ring=raw.faces[n];row.quality=metrics(oracle,x);
            row.installedOwner=nativeCells[oracle.faceOwner()[x]];
            row.installedNeighbour=x<oracle.nInternalFaces()?nativeCells[oracle.faceNeighbour()[x]]:-1;
            if(row.zone>=0) {valid=false;break;}
            for(scalar q:row.quality) if(!finite(q)||q<0) valid=false;
            for(label y:row.ring) {
                if(y<0||y>=label(raw.points.size())||map.alias[y]<0
                    ||map.alias[y]>=label(raw.points.size())||!finite(raw.points[y])) {valid=false;break;}
                row.rawCoordinates.push_back(raw.points[y]);
                p.aliases[y]={map.alias[y],raw.points[map.alias[y]]};
            }
            for(label y:oracle.faces()[x]) row.installedCoordinates.push_back(oracle.points()[y]);
            if(row.installedCoordinates.size()!=row.ring.size()) {valid=false;break;}
            if(x>=oracle.nInternalFaces())
                row.physicalPatchRole=patchRole(oracle.boundaryMesh()[oracle.boundaryMesh().patchID(x)]);
            p.payload+=sizeof(RawFace)+row.ring.size()*(sizeof(label)+2*sizeof(point))+row.physicalPatchRole.size();
            p.faces.push_back(std::move(row));
        }
        if(!valid) continue;
        p.payload+=p.cells.size()*sizeof(RawCell)+p.aliases.size()*(sizeof(label)*2+sizeof(point))
            +(p.key.size()+p.keeperKeys[0].size()+p.keeperKeys[1].size())*sizeof(std::tuple<scalar,scalar,scalar>);
        for(const auto& o:p.origins)
            p.payload+=sizeof(Origin)+o.region.size()+o.sourceFacets.size()*sizeof(std::array<label,3>);
        for(const auto& c:p.cells) p.payload+=c.rawFaces.size()*sizeof(label);
        if(state.payload+p.payload>16u*1024u*1024u) break;
        state.payload+=p.payload;state.pool.push_back(std::move(p));
    }
}

inline bool noWriterPairTies(const Packet& p,label keep,label drop) {
    std::set<std::pair<label,label>> pairs;
    for(const RawFace& f:p.faces) if(f.id!=p.nativeFace && f.neighbour>=0) {
        label o=f.owner==drop?keep:f.owner,n=f.neighbour==drop?keep:f.neighbour;
        if(o==n||!pairs.insert(std::minmax(o,n)).second) return false;
    }
    return true;
}

// The old native raw loop may differ from the already normalized oracle loop.
// Prove the exact ring expected by THIS writer/canonical-owner path before
// committing. Unknown raw-to-installed point0/alias arithmetic refuses a keeper.
inline bool writerShellSupported(const Packet& p,label keep,label drop) {
    for(const auto& f:p.faces)
        if(std::find(p.outer.begin(),p.outer.end(),f.id)!=p.outer.end()) {
            std::vector<point> rawExpected;
            for(label n:f.ring) {
                const auto a=p.aliases.find(n);if(a==p.aliases.end()) return false;
                rawExpected.push_back(a->second.second);
            }
            const label ro=f.owner==drop?keep:f.owner;
            const label rn=f.neighbour==drop?keep:f.neighbour;
            // Existing union canonicalization tail-reverses before writer.
            if(rn>=0&&ro>rn) std::reverse(rawExpected.begin()+1,rawExpected.end());
            auto installedExpected=f.installedCoordinates;
            const label io=f.installedOwner==drop?keep:f.installedOwner;
            const label in=f.installedNeighbour==drop?keep:f.installedNeighbour;
            if(in>=0&&io>in)
                std::reverse(installedExpected.begin()+1,installedExpected.end());
            if(rawExpected.size()!=installedExpected.size()) return false;
            for(std::size_t j=0;j<rawExpected.size();++j)
                if(!bits(rawExpected[j],installedExpected[j])) return false;
        }
    return true;
}

inline bool dependency
(
    const Packet& p,const Cut& m,const std::vector<std::vector<label>>& cf
) {
    for(const auto& c:p.cells) {
        if(c.id<0||c.id>=m.nCells||!m.cellAlive[c.id]) return false;
        auto faces=cf[c.id];std::sort(faces.begin(),faces.end());
        if(faces!=c.rawFaces) return false;
    }
    for(const auto& f:p.faces) {
        if(f.id<0||f.id>=label(m.faces.size())||!m.faceAlive[f.id]
          ||m.owner[f.id]!=f.owner||m.neighbour[f.id]!=f.neighbour
          ||m.patch[f.id]!=f.patch||m.zone[f.id]!=f.zone||m.faces[f.id]!=f.ring) return false;
        for(std::size_t j=0;j<f.ring.size();++j)
            if(!bits(m.points[f.ring[j]],f.rawCoordinates[j])) return false;
    }
    return true;
}
inline bool cellGeometry(const polyMesh& mesh,label c) {
    if(c<0||c>=mesh.nCells()||!finite(mesh.cellCentres()[c])
       ||!finite(mesh.cellVolumes()[c])||mesh.cellVolumes()[c]<=VSMALL) return false;
    std::map<std::pair<label,label>,std::pair<label,label>> edges;
    vector closure(Zero);scalar area=0;std::set<label> vertices;
    point lo(GREAT,GREAT,GREAT),hi(-GREAT,-GREAT,-GREAT);
    for(label f:mesh.cells()[c]) for(label p:mesh.faces()[f]) {
        vertices.insert(p);lo=min(lo,mesh.points()[p]);hi=max(hi,mesh.points()[p]);
    }
    const scalar span=max(mag(hi-lo),scalar(1e-6));
    for(label f:mesh.cells()[c]) {
        const bool owner=mesh.faceOwner()[f]==c;
        const vector sf=(owner?1:-1)*mesh.faceAreas()[f];
        const point fc=mesh.faceCentres()[f];
        if(!finite(sf)||!finite(fc)||mag(sf)<=1e-12) return false;
        closure+=sf;area+=mag(sf);const vector normal=sf/mag(sf);
        if((sf&(fc-mesh.cellCentres()[c]))<=0) return false;
        const auto& ring=mesh.faces()[f];
        forAll(ring,j) {
            label a=ring[j],b=ring[(j+1)%ring.size()];if(!owner) std::swap(a,b);
            auto& e=edges[std::minmax(a,b)];++e.first;e.second+=a<b?1:-1;
            if(!finite(mesh.points()[a])||mag((mesh.points()[a]-fc)&normal)>1e-10*span) return false;
        }
        for(label p:vertices) if(((mesh.points()[p]-fc)&normal)>1e-10*span) return false;
        for(label other:mesh.cells()[c]) if(other!=f) {
            const vector delta=mesh.faceCentres()[other]-fc;
            if((normal&delta)/max(mag(delta),VSMALL)>-1e-6) return false;
        }
    }
    for(const auto& e:edges) if(e.second.first!=2||e.second.second!=0) return false;
    return mag(closure)/max(area,VSMALL)<=1e-12;
}
inline bool tets(const polyMesh& mesh,label f) {
    if(coupled(mesh,f)) return false;
    const scalar tolerance=sqr(SMALL); // installed polyMeshTetDecomposition minTetQuality
    const auto& poly=mesh.faces()[f];const point fc=mesh.faceCentres()[f];
    const point own=mesh.cellCentres()[mesh.faceOwner()[f]];
    const bool internal=f<mesh.nInternalFaces();
    const point nei=internal?mesh.cellCentres()[mesh.faceNeighbour()[f]]:point::zero;
    forAll(poly,j) {
        const point a=mesh.points()[poly[j]],b=mesh.points()[poly[(j+1)%poly.size()]];
        const scalar oq=tetPointRef(a,b,fc,own).quality();
        const scalar nq=internal?tetPointRef(a,b,fc,nei).quality():1;
        if(!finite(oq)||!finite(nq)||oq>=-tolerance||nq<=tolerance) return false;
    }
    for(label k=0;k<poly.size();++k) {
        bool ok=true;
        for(label j=1;j+1<poly.size();++j) {
            const point a=mesh.points()[poly[k]],b=mesh.points()[poly[(k+j)%poly.size()]];
            const point c=mesh.points()[poly[(k+j+1)%poly.size()]];
            const scalar oq=tetPointRef(a,b,c,own).quality();
            const scalar nq=internal?tetPointRef(a,b,c,nei).quality():1;
            if(!finite(oq)||!finite(nq)||oq>=-tolerance||nq<=tolerance) {ok=false;break;}
        }
        if(ok) return true;
    }
    return false;
}
inline label mappedCell(const Bindings& b,label c) {
    return c>=0&&c<label(b.cell.size())?b.cell[c]:-1;
}
// Real final installed oracle. No ordinary CC/V memcmp; no hard epsilon.
// All physical rings and the integer oriented exterior chain stay literal:
// this proves exact rational V/M1 identities without a new arithmetic library.
inline std::string physicalRoleAfter(const std::string& original,label pruned) {
    if(!pruned) return original;
    IStringStream input(original);dictionary role(input);
    const label before=role.get<label>("nFaces"); // installed polyPatch supplied this typed field
    if(pruned<0||before<pruned) return "<invalid physical cardinality remap>";
    role.set("nFaces",before-pruned);
    OStringStream out;out.precision(std::numeric_limits<scalar>::max_digits10);
    role.writeEntries(out);return out.str();
}

// Exact mathematical-shape proof. No actual quality threshold or count is changed.
inline bool literalUnchangedWeight
(const Packet& p,const RawFace& target,const Cut& raw,const polyMesh& mesh,
 const Bindings& b,State& state)
{
    if(target.installedNeighbour<0||target.id==p.nativeFace) return false;
    using namespace dcCartLiteralWeightProof;
    Shape oldShape[2],newShape[2];
    const label ids[2]={target.installedOwner,target.installedNeighbour};
    if(mappedCell(b,ids[0])<0||mappedCell(b,ids[0])==mappedCell(b,ids[1]))return false;
    for(unsigned side=0;side<2;++side) {
        const label id=ids[side];
        if(id==p.a||id==p.b||id==p.keep||id==p.drop) return false;
        const RawCell* cell=nullptr;
        for(const auto& c:p.cells) if(c.id==id) {if(cell)return false;cell=&c;}
        const label dest=mappedCell(b,id);
        if(!cell||dest<0||zonedCell(mesh,dest)) return false;
        auto expected=cell->rawFaces;std::sort(expected.begin(),expected.end());
        if(std::adjacent_find(expected.begin(),expected.end())!=expected.end())return false;
        std::vector<label> compact;
        for(label f:expected) {if(f<0||f>=label(b.face.size())||b.face[f]<0)return false;compact.push_back(b.face[f]);}
        std::sort(compact.begin(),compact.end());
        auto now=mesh.cells()[dest];std::sort(now.begin(),now.end());
        if(compact.size()!=size_t(now.size())||!std::equal(compact.begin(),compact.end(),now.begin()))return false;
        oldShape[side].native=newShape[side].native=id;
        oldShape[side].complete=newShape[side].complete=true;
        for(label n:expected) {
            const RawFace* f=nullptr;for(const auto& row:p.faces)if(row.id==n){if(f)return false;f=&row;}
            if(!f||n==p.nativeFace||f->zone>=0)return false;
            const label x=b.face[n];
            if(coupled(mesh,x)||zonedFace(mesh,x))return false;
            // Only the known drop-to-keep incidence remap is authorized.
            // Compare the outside endpoint's literal installed facets; no core CC/V is read.
            const label oo=mappedCell(b,f->installedOwner==p.drop?p.keep:f->installedOwner),
                on=f->installedNeighbour<0?-1:mappedCell(b,
                    f->installedNeighbour==p.drop?p.keep:f->installedNeighbour);
            if(oo<0||(f->installedNeighbour>=0&&on<0))return false;
            const bool flip=on>=0&&oo>on;
            Facet old,next;old.native=next.native=n;old.owner=flip?on:oo;old.neighbour=flip?oo:on;
            next.owner=mesh.faceOwner()[x];next.neighbour=x<mesh.nInternalFaces()?mesh.faceNeighbour()[x]:-1;
            old.zone=f->zone;next.zone=-1;
            old.ring=f->installedCoordinates;if(flip)std::reverse(old.ring.begin()+1,old.ring.end());
            for(label q:mesh.faces()[x]){if(zonedPoint(mesh,q))return false;next.ring.push_back(mesh.points()[q]);}
            if(f->patch>=0) {
                if(x<mesh.nInternalFaces())return false;
                old.physicalRole=physicalRoleAfter(f->physicalPatchRole,state.authorizedPhysicalPruned[f->patch]);
                next.physicalRole=patchRole(mesh.boundaryMesh()[mesh.boundaryMesh().patchID(x)]);
            } else if(x>=mesh.nInternalFaces())return false;
            std::vector<label> nativeRing=f->ring;if(flip)std::reverse(nativeRing.begin()+1,nativeRing.end());
            if(nativeRing.size()!=old.ring.size())return false;
            for(size_t ordinal=0;ordinal<nativeRing.size();++ordinal) {
                const label q=nativeRing[ordinal];
                const auto a=p.aliases.find(q);
                if(a==p.aliases.end()||q<0||q>=label(b.alias.size())||b.alias[q]<0||b.alias[q]>=label(raw.points.size()))return false;
                if(q>=label(b.point.size())||b.alias[q]>=label(b.point.size())
                   ||b.point[q]<0||b.point[q]>=mesh.nPoints()
                   ||b.point[q]!=b.point[b.alias[q]]
                   ||ordinal>=size_t(mesh.faces()[x].size())
                   ||mesh.faces()[x][ordinal]!=b.point[q]
                   ||!bits(mesh.points()[b.point[q]],a->second.second))return false;
                Alias e;e.original=a->second.first;e.current=e.original;e.before=e.after=a->second.second;
                Alias v=e;v.current=b.alias[q];v.after=raw.points[b.alias[q]];
                old.aliases.push_back(e);next.aliases.push_back(v);
            }
            oldShape[side].faces.push_back(old);newShape[side].faces.push_back(next);
        }
    }
    return proof(oldShape[0],oldShape[1],newShape[0],newShape[1]);
}

inline label verify
(
    State& state,const Cut& raw,const polyMesh& mesh,const Bindings& b,
    bool literalShell,bool capturePrefix
) {
    label failures=0;
    for(const Packet& p:state.committed) {
        bool ok=true;Stats old,next;scalar geometricComparisonWorstW=0;label equivalentBadW=0;
        const char* firstRefusal="none";label firstDetail=-1;
        auto refuse=[&](const char* reason,label detail)
        { if(ok) {firstRefusal=reason;firstDetail=detail;}ok=false; };
        const label keep=mappedCell(b,p.keep);
        if(keep<0||mappedCell(b,p.drop)>=0||mesh.cells()[keep].size()!=6) {
            ++failures;Pout<<"W installed packet refused: keep/drop "<<p.keep<<'/'<<p.drop
                <<", shared "<<p.nativeFace<<", first reason survivor-map-or-six-face-shell"<<endl;continue;
        }
        std::set<label> counted;
        for(const RawCell& c:p.cells) {
            const label dest=mappedCell(b,c.id==p.drop?p.keep:c.id);
            old.add(c.d,0);
            if(dest<0||zonedCell(mesh,dest)||!cellGeometry(mesh,dest)) {refuse("cell-map-zone-geometry",c.id);break;}
            const scalar d=determinant(mesh,dest);
            if(pass(c.d,0)&&!pass(d,0)) refuse("passing-cell-determinant",c.id);
            if(counted.insert(dest).second) next.add(d,0);
        }
        if(!pass(determinant(mesh,keep),0)) refuse("survivor-determinant",p.keep);
        for(const auto& alias:p.aliases) {
            const label n=alias.first;
            if(n<0||n>=label(b.alias.size())||b.alias[n]!=alias.second.first
              ||b.alias[n]<0||b.alias[n]>=label(raw.points.size())
              ||(literalShell&&!bits(raw.points[b.alias[n]],alias.second.second))) {refuse("point-alias-or-coordinate",n);break;}
        }
        for(const RawFace& f:p.faces) {
            for(unsigned k=0;k<4;++k) old.add(f.quality[k],k+1);
            if(f.id==p.nativeFace) {
                // Deleted passing R/S/N roles inherit every literal shell interface.
                for(label n:p.outer) {
                    if(n<0||n>=label(b.face.size())||b.face[n]<0) {refuse("deleted-interface-inherited-map",n);break;}
                    const auto q=metrics(mesh,b.face[n]);
                    for(unsigned k=1;k<4;++k)
                        if(pass(f.quality[k],k+1)&&!pass(q[k],k+1)) refuse("passing-face-quality",f.id);
                }
                if(f.id>=label(b.face.size())||b.face[f.id]>=0) refuse("deleted-shared-face-still-mapped",f.id);
                continue;
            }
            if(f.id<0||f.id>=label(b.face.size())||b.face[f.id]<0) {refuse("retained-face-map",f.id);break;}
            const label x=b.face[f.id];
            if(coupled(mesh,x)||zonedFace(mesh,x)||!tets(mesh,x)) {refuse("face-coupled-zone-tets",f.id);break;}
            const label oldO=mappedCell(b,f.installedOwner==p.drop?p.keep:f.installedOwner);
            const label oldN=f.installedNeighbour<0?-1:mappedCell(b,
                f.installedNeighbour==p.drop?p.keep:f.installedNeighbour);
            const label nowO=mesh.faceOwner()[x],nowN=x<mesh.nInternalFaces()?mesh.faceNeighbour()[x]:-1;
            const bool flip=oldN>=0&&oldO>oldN;
            if(nowO!=(flip?oldN:oldO)||nowN!=(flip?oldO:oldN)) {refuse("canonical-owner-neighbour",f.id);break;}
            auto expected=f.installedCoordinates;
            if(flip) std::reverse(expected.begin()+1,expected.end()); // point0 remains explicit
            const auto& ring=mesh.faces()[x];
            if(ring.size()!=label(expected.size())) {refuse("ring-size",f.id);break;}
            if(literalShell||f.patch>=0)
                forAll(ring,j) if(!bits(mesh.points()[ring[j]],expected[j])) refuse("ordered-ring-coordinate",f.id);
            if(f.patch>=0) {
                if(x<mesh.nInternalFaces()||patchRole(mesh.boundaryMesh()[mesh.boundaryMesh().patchID(x)])
                      !=physicalRoleAfter(f.physicalPatchRole,state.authorizedPhysicalPruned[f.patch]))
                    refuse("physical-patch-role",f.id);
            } else if(x>=mesh.nInternalFaces()) refuse("internal-face-now-boundary",f.id);
            const auto q=metrics(mesh,x);
            scalar comparisonW=severity(q[0],1);
            if(!pass(f.quality[0],1)&&!pass(q[0],1)
               &&dcCartLiteralWeightProof::credit(f.quality[0],q[0],scalar(.05))
               &&literalUnchangedWeight(p,f,raw,mesh,b,state))
            { comparisonW=severity(f.quality[0],1);++equivalentBadW; }
            geometricComparisonWorstW=max(geometricComparisonWorstW,comparisonW);
            for(unsigned k=0;k<4;++k) {
                if(pass(f.quality[k],k+1)&&!pass(q[k],k+1)) refuse("passing-face-quality",f.id);
                next.add(q[k],k+1);
            }
        }
        // Actual installed totals and worst severity. These are not compact expectations.
        for(unsigned k=0;k<5;++k)
            if(next.bad[k]>old.bad[k]||(k==1?geometricComparisonWorstW:next.worst[k])>old.worst[k]) refuse("bad-count-or-worst-severity",k);
        if(next.bad[0]+next.bad[1]>=old.bad[0]+old.bad[1]) refuse("no-installed-DW-count-decrease",-1);
        if(!ok) {
            ++failures;OStringStream diagnostic;diagnostic.precision(std::numeric_limits<scalar>::max_digits10);
            diagnostic<<"W installed packet refused: keep/drop "<<p.keep<<'/'<<p.drop<<", shared "<<p.nativeFace
                <<", first reason "<<firstRefusal<<", native detail "<<firstDetail
                <<", W exact-equivalent bad faces "<<equivalentBadW<<", W comparison worst "<<geometricComparisonWorstW<<", literal/prefix "<<literalShell<<'/'<<capturePrefix<<", old/new counts ";
            for(unsigned k=0;k<5;++k)diagnostic<<old.bad[k]<<'/'<<next.bad[k]<<' ';
            diagnostic<<", old/new worst ";for(unsigned k=0;k<5;++k)diagnostic<<old.worst[k]<<'/'<<next.worst[k]<<' ';
            Pout<<diagnostic.str()<<endl;
        }
        else {
            OStringStream diagnostic;diagnostic.precision(std::numeric_limits<scalar>::max_digits10);
            diagnostic<<"W installed packet passed: keep/drop "<<p.keep<<'/'<<p.drop
                <<", shared "<<p.nativeFace<<", proved bad W "<<equivalentBadW
                <<", comparison W worst "<<geometricComparisonWorstW
                <<", actual W worst "<<next.worst[1]<<", actual old/new counts ";
            for(unsigned k=0;k<5;++k)diagnostic<<old.bad[k]<<'/'<<next.bad[k]<<' ';
            diagnostic<<", actual old/new worst ";
            for(unsigned k=0;k<5;++k)diagnostic<<old.worst[k]<<'/'<<next.worst[k]<<' ';
            Pout<<diagnostic.str()<<endl;
        }
        if(ok&&capturePrefix) {
            for(const auto& c:p.cells) {
                const label native=c.id==p.drop?p.keep:c.id;
                const label compact=mappedCell(b,native);
                state.installedPrefixCells[native]=determinant(mesh,compact);
            }
            for(const auto& f:p.faces) if(f.id!=p.nativeFace)
                state.installedPrefixFaces[f.id]=metrics(mesh,b.face[f.id]);
        }
    }
    if(!capturePrefix) {
        // Point repair is allowed to reshape interior geometry, but every
        // actually installed W-prefix passing role must survive its FINAL state.
        for(const auto& row:state.installedPrefixCells) {
            const label c=mappedCell(b,row.first);
            if(c<0||(pass(row.second,0)&&!pass(determinant(mesh,c),0))) ++failures;
        }
        for(const auto& row:state.installedPrefixFaces) {
            const label f=row.first>=0&&row.first<label(b.face.size())?b.face[row.first]:-1;
            if(f<0) {++failures;continue;}
            const auto q=metrics(mesh,f);
            for(unsigned k=0;k<4;++k)
                if(pass(row.second[k],k+1)&&!pass(q[k],k+1)) ++failures;
        }
    }
    return failures;
}
} // namespace dcCartWeightUnionPreview
struct dcCartCutCells::writerWeightUnionState : dcCartWeightUnionPreview::State {};
} // namespace Foam
#endif
