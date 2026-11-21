// Bounded, default-off shared interior point proposals.
// Trial calculations never communicate or write. The installed mesh is the
// final geometry oracle, followed by the caller's earliest writer certificate.
#ifndef dcCartFinitePointRepair_H
#define dcCartFinitePointRepair_H
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <functional>
#include <limits>
#include <map>
#include <set>
#include <vector>

namespace dcCartFiniteRepairKernel
{
using Row = std::vector<double>;
using Matrix = std::vector<Row>;
inline bool finite(const Row& a)
{
    for (double x : a) if (!std::isfinite(x)) return false;
    return true;
}
inline double dot(const Row& a, const Row& b)
{
    double v=0;
    for (std::size_t i=0; i<a.size(); ++i) v+=a[i]*b[i];
    return v;
}
template<class Labels,class Zones>
inline bool intersectsCellZones(const Labels& participating,const Zones& zones)
{
    for (const auto& zone:zones)
        for (const auto cell:zone) if (participating.count(cell)) return true;
    return false;
}
template<class Scalar>
inline bool preservesHardRoles
(
    const std::vector<Scalar>& before,const std::vector<Scalar>& after,
    Scalar threshold,bool upper,Scalar tiny
)
{
    if (before.size()!=after.size() || !std::isfinite(double(threshold))
     || threshold<=0 || !std::isfinite(double(tiny)) || tiny<=0) return false;
    unsigned oldBad=0,newBad=0;Scalar oldWorst=0,newWorst=0;
    for (std::size_t i=0;i<before.size();++i)
    {
        if (!std::isfinite(double(before[i])) || !std::isfinite(double(after[i]))) return false;
        const bool oldPass=upper ? before[i]<=threshold : before[i]>=threshold;
        const bool newPass=upper ? after[i]<=threshold : after[i]>=threshold;
        if (oldPass && !newPass) return false;
        oldBad+=!oldPass;newBad+=!newPass;
        oldWorst=std::max(oldWorst,upper ? before[i]/threshold-1
            : threshold/std::max(before[i],tiny)-1);
        newWorst=std::max(newWorst,upper ? after[i]/threshold-1
            : threshold/std::max(after[i],tiny)-1);
    }
    return newBad<=oldBad && newWorst<=oldWorst;
}
struct Projection
{
    Row step;
    bool feasible=false;
    unsigned updates=0;
};
inline Projection project
(
    const Row& desired, const Matrix& input, const Row& bounds,
    unsigned maxUpdates=384
)
{
    Projection out;
    out.step.assign(desired.size(), 0);
    if (desired.empty() || input.size()!=bounds.size()
     || !finite(desired) || !finite(bounds)) return out;
    Matrix a; Row b;
    for (std::size_t i=0; i<input.size(); ++i)
    {
        if (input[i].size()!=desired.size() || !finite(input[i])) return out;
        const double n=std::sqrt(dot(input[i],input[i]));
        if (!std::isfinite(n)) return out;
        if (n<=1e-13)
        {
            if (bounds[i]<-2e-10) return out;
            continue;
        }
        Row row=input[i];
        for (double& x:row) x/=n;
        a.push_back(row); b.push_back(bounds[i]/n);
    }
    out.step=desired;
    if (a.empty()) { out.feasible=true; return out; }
    Row dual(a.size(),0), slack(a.size());
    for (std::size_t i=0; i<a.size(); ++i) slack[i]=dot(a[i],out.step)+b[i];
    for (unsigned j=0; j<maxUpdates; ++j)
    {
        std::size_t chosen=0; double delta=0, largest=-1;
        for (std::size_t i=0; i<a.size(); ++i)
        {
            const double change=std::max(0.0,dual[i]-slack[i])-dual[i];
            if (std::fabs(change)>largest)
            { largest=std::fabs(change); chosen=i; delta=change; }
        }
        out.updates=j+1;
        if (largest<2e-10) break;
        dual[chosen]+=delta;
        for (std::size_t k=0; k<out.step.size(); ++k)
            out.step[k]+=delta*a[chosen][k];
        for (std::size_t i=0; i<a.size(); ++i)
            slack[i]+=delta*dot(a[i],a[chosen]);
        if (!finite(out.step) || !finite(slack)) return Projection{};
    }
    out.feasible=finite(out.step);
    for (std::size_t i=0; i<a.size(); ++i)
        if (dot(a[i],out.step)+b[i]<-2e-8) out.feasible=false;
    return out;
}
struct Evaluation
{
    double objective=0;
    Row constraints;
    bool valid=false;
};
struct Search
{
    Row parameters;
    unsigned requests=0, iterations=0, projectionUpdates=0;
    bool improved=false, stoppedByBudget=false;
};
inline Search propose
(
    unsigned dimension,
    const std::function<Evaluation(const Row&)>& evaluate,
    const std::function<bool()>& available
)
{
    Search out; out.parameters.assign(dimension,0);
    if (dimension!=3 && dimension!=4) return out;
    auto request=[&](const Row& q, Evaluation& e)
    {
        if (out.requests>=256 || !available())
        { out.stoppedByBudget=true; return false; }
        ++out.requests; e=evaluate(q);
        return e.valid && std::isfinite(e.objective)
            && !e.constraints.empty() && finite(e.constraints);
    };
    Row q(dimension,0); Evaluation current;
    if (!request(q,current)) return out;
    for (unsigned iteration=0; iteration<32; ++iteration)
    {
        Matrix jac(current.constraints.size(),Row(dimension,0));
        Row grad(dimension,0); bool valid=true;
        for (unsigned k=0; k<dimension; ++k)
        {
            Row plus=q, minus=q; plus[k]+=1e-5; minus[k]-=1e-5;
            Evaluation ep,em;
            if (!request(plus,ep) || !request(minus,em)
             || ep.constraints.size()!=current.constraints.size()
             || em.constraints.size()!=current.constraints.size())
            { valid=false; break; }
            grad[k]=(ep.objective-em.objective)/2e-5;
            for (std::size_t i=0; i<jac.size(); ++i)
                jac[i][k]=(ep.constraints[i]-em.constraints[i])/2e-5;
        }
        if (!valid) break;
        const double n=std::sqrt(dot(grad,grad));
        if (!std::isfinite(n) || n<1e-12) break;
        bool accepted=false;
        for (double radius:{.004,.002,.001,.0005})
        {
            Row desired=grad;
            for (double& x:desired) x*=-radius/n;
            Matrix a=jac; Row b=current.constraints;
            for (unsigned k=0; k<dimension; ++k)
            {
                Row row(dimension,0); row[k]=1;
                a.push_back(row); b.push_back(q[k]+.08);
            }
            for (unsigned k=0; k<dimension; ++k)
            {
                Row row(dimension,0); row[k]=-1;
                a.push_back(row); b.push_back(.08-q[k]);
            }
            const Projection projected=project(desired,a,b);
            out.projectionUpdates+=projected.updates;
            if (!projected.feasible) continue;
            for (double fraction:{1.0,.5,.25,.125,.0625,.03125})
            {
                Row trial=q;
                for (unsigned k=0; k<dimension; ++k)
                    trial[k]+=fraction*projected.step[k];
                Evaluation next;
                if (!request(trial,next)) { valid=false; break; }
                if (next.constraints.size()!=current.constraints.size())
                { valid=false; break; }
                if (*std::min_element(next.constraints.begin(),next.constraints.end())>=-2e-9
                 && next.objective<current.objective-1e-8)
                {
                    q=trial; current=next; accepted=true;
                    out.parameters=q; out.improved=true; break;
                }
            }
            if (!valid || accepted) break;
        }
        ++out.iterations;
        if (!valid || !accepted || -current.objective*.01>=.0105) break;
    }
    return out;
}

using XYZ = std::array<double,3>;
inline bool same(double a,double b) { return std::memcmp(&a,&b,sizeof(double))==0; }
struct Face
{
    std::vector<int> points;
    bool protectedFace=false;
};
struct Mode
{
    int pivot=-1, pairA=-1, pairB=-1, axis=-1;
    unsigned dimension=0;
};
inline Mode select
(
    const std::vector<XYZ>& points, const std::vector<Face>& faces,
    const std::vector<std::vector<int>>& pointFaces, int target
)
{
    Mode out;
    if (target<0 || target>=int(faces.size())
     || pointFaces.size()!=points.size() || faces[target].protectedFace
     || faces[target].points.size()!=3) return out;
    auto interior=[&](int p, bool triangles)
    {
        if (p<0 || p>=int(points.size()) || pointFaces[p].empty()) return false;
        for (double x:points[p]) if (!std::isfinite(x)) return false;
        for (int f:pointFaces[p])
            if (f<0 || f>=int(faces.size()) || faces[f].protectedFace
             || (triangles && faces[f].points.size()!=3)) return false;
        return true;
    };
    for (int p:faces[target].points)
        if (interior(p,true) && (out.pivot<0 || points[p]<points[out.pivot]))
            out.pivot=p;
    if (out.pivot<0) return out;
    out.dimension=3;
    std::vector<int> pair;
    for (int p:faces[target].points) if (p!=out.pivot) pair.push_back(p);
    if (pair.size()!=2 || !interior(pair[0],false) || !interior(pair[1],false))
        return out;
    if (points[pair[1]]<points[pair[0]]) std::swap(pair[0],pair[1]);
    std::set<int> incident;
    incident.insert(pointFaces[pair[0]].begin(),pointFaces[pair[0]].end());
    incident.insert(pointFaces[pair[1]].begin(),pointFaces[pair[1]].end());
    for (int axis=0; axis<3; ++axis)
    {
        if (!same(points[pair[0]][axis],points[pair[1]][axis])) continue;
        bool valid=true, polygon=false;
        for (int fi:incident)
        {
            const Face& f=faces[fi];
            if (f.points.size()==3) continue;
            polygon=true;
            bool constant[3]={true,true,true}; unsigned touches=0;
            for (int p:f.points)
            {
                if (p<0 || p>=int(points.size())) { valid=false; break; }
                if (p==pair[0] || p==pair[1]) ++touches;
                for (int a=0;a<3;++a)
                    constant[a]=constant[a] && same(points[p][a],points[f.points[0]][a]);
            }
            bool allowed=false;
            for (int a=0;a<3;++a) if (a!=axis && constant[a]) allowed=true;
            if (!allowed && constant[axis] && touches==2)
            {
                for (int a=0;a<3;++a) if (a!=axis)
                {
                    if (!same(points[pair[0]][a],points[pair[1]][a])) continue;
                    int fixed=-1; bool level=true;
                    for (int p:f.points) if (p!=pair[0] && p!=pair[1])
                    {
                        if (fixed<0) fixed=p;
                        else level=level && same(points[fixed][a],points[p][a]);
                    }
                    if (fixed>=0 && level
                     && !same(points[fixed][a],points[pair[0]][a])) allowed=true;
                }
            }
            if (!allowed || !valid) { valid=false; break; }
        }
        if (valid && polygon)
        {
            out.axis=axis; out.pairA=pair[0]; out.pairB=pair[1];
            out.dimension=4; return out;
        }
    }
    return out;
}
} // namespace dcCartFiniteRepairKernel

#ifndef DCCART_FINITE_REPAIR_KERNEL_ONLY
// The production adapter below deliberately has no optimizer/library dependency.
#include "polyMesh.H"
#include "pointIOField.H"
#include "primitiveMeshTools.H"
#include "polyMeshTools.H"
#include "mathematicalConstants.H"
#include "tetPointRef.H"
#include "polyMeshTetDecomposition.H"
#include "PstreamReduceOps.H"
#include "symmTensor.H"
#include "faceZoneMesh.H"
#include "pointZoneMesh.H"
#include "cellZoneMesh.H"

namespace Foam
{
namespace dcCartFinitePointRepair
{
struct Result
{
    label proposed=0, committed=0, refused=0, requests=0;
    label budgetStopped=0, unsupported=0, finalRolledBack=0;
    scalar setupSeconds=0, proposalSeconds=0, installedSeconds=0;
    scalar guardSeconds=0, rollbackSeconds=0, elapsedSeconds=0;
};

inline bool finiteScalar(scalar x) { return std::isfinite(double(x)); }
inline bool finiteVector(const vector& x)
{
    return finiteScalar(x.x()) && finiteScalar(x.y()) && finiteScalar(x.z());
}
inline bool sameScalar(scalar a,scalar b)
{
    return std::memcmp(&a,&b,sizeof(scalar))==0;
}
inline bool sameVector(const vector& a,const vector& b)
{
    for (direction k=0;k<3;++k) if (!sameScalar(a[k],b[k])) return false;
    return true;
}
struct FaceData
{
    label id=-1, owner=-1, neighbour=-1;
    face vertices; // compact point indices, original ring order
    bool coupled=false;
};
struct CellData
{
    label id=-1;
    std::vector<label> faces; // original owner faces, then neighbour faces
};
struct State
{
    std::vector<vector> sf,fc,cc;
    std::vector<scalar> volume,determinant;
    std::vector<std::array<scalar,4>> faceQuality; // W,R,S,N
    bool valid=true;
};
struct Stencil
{
    const polyMesh& mesh;
    std::vector<label> pointIDs,faceIDs,cellIDs;
    std::map<label,label> pointIndex,faceIndex,cellIndex;
    std::vector<FaceData> faces;
    faceList compactFaces;
    std::vector<CellData> cells;
    std::vector<label> core,outside;
    std::map<label,std::pair<vector,scalar>> halo;
    pointField original;
    scalar scale=0;
    bool eligible=true;

    Stencil
    (
        const polyMesh& input, const pointField& points,
        const std::vector<label>& moving,
        const std::map<label,std::pair<vector,scalar>>& currentCells={}
    ) : mesh(input)
    {
        const auto& pf=mesh.pointFaces();
        const auto& cf=mesh.cells();
        const auto& own=mesh.faceOwner();
        const auto& nei=mesh.faceNeighbour();
        std::set<label> coreSet,outsideSet,faceSet,pointSet;
        for (label p:moving)
        {
            if (p<0 || p>=points.size()) { eligible=false; return; }
            for (label f:pf[p])
            {
                coreSet.insert(own[f]);
                if (f<nei.size()) coreSet.insert(nei[f]);
                if (coreSet.size()>128) { eligible=false;return; }
            }
        }
        for (label c:coreSet) for (label f:cf[c])
        {
            faceSet.insert(f);
            if (f<nei.size())
            {
                const label other=(own[f]==c ? nei[f] : own[f]);
                if (!coreSet.count(other)) outsideSet.insert(other);
            }
            else if (mesh.boundaryMesh()[mesh.boundaryMesh().patchID(f)].coupled())
            { eligible=false; return; }
        }
        if (outsideSet.size()>256) { eligible=false;return; }
        std::set<label> allCells=coreSet;
        allCells.insert(outsideSet.begin(),outsideSet.end());
        if (dcCartFiniteRepairKernel::intersectsCellZones(allCells,mesh.cellZones()))
        { eligible=false;return; }
        for (label c:outsideSet) for (label f:cf[c])
        {
            faceSet.insert(f);
            if (faceSet.size()>2048) { eligible=false;return; }
        }
        for (label f:faceSet) for (label p:mesh.faces()[f])
        {
            pointSet.insert(p);
            if (pointSet.size()>8192) { eligible=false;return; }
        }
        pointIDs.assign(pointSet.begin(),pointSet.end());
        faceIDs.assign(faceSet.begin(),faceSet.end());
        cellIDs.assign(allCells.begin(),allCells.end());
        original.setSize(pointIDs.size());
        vector lo(GREAT,GREAT,GREAT),hi(-GREAT,-GREAT,-GREAT);
        for (label i=0;i<label(pointIDs.size());++i)
        {
            pointIndex[pointIDs[i]]=i; original[i]=points[pointIDs[i]];
            if (!finiteVector(original[i])) { eligible=false; return; }
            for (direction k=0;k<3;++k)
            { lo[k]=min(lo[k],original[i][k]); hi[k]=max(hi[k],original[i][k]); }
        }
        scale=max(hi.x()-lo.x(),max(hi.y()-lo.y(),hi.z()-lo.z()));
        if (!finiteScalar(scale) || scale<=3e-6) { eligible=false; return; }
        for (label i=0;i<label(cellIDs.size());++i) cellIndex[cellIDs[i]]=i;
        for (label i=0;i<label(faceIDs.size());++i)
        {
            label f=faceIDs[i]; faceIndex[f]=i;
            FaceData data; data.id=f; data.owner=own[f];
            data.neighbour=(f<nei.size() ? nei[f] : -1);
            if (f>=nei.size())
                data.coupled=mesh.boundaryMesh()[mesh.boundaryMesh().patchID(f)].coupled();
            data.vertices.setSize(mesh.faces()[f].size());
            forAll(data.vertices,j) data.vertices[j]=pointIndex.at(mesh.faces()[f][j]);
            faces.push_back(data);
        }
        compactFaces.setSize(faces.size());
        forAll(compactFaces,f) compactFaces[f]=faces[f].vertices;
        for (label c:cellIDs)
        {
            CellData data; data.id=c;
            for (label f:cf[c]) data.faces.push_back(faceIndex.at(f));
            cells.push_back(data);
        }
        for (label c:coreSet) core.push_back(cellIndex.at(c));
        for (label c:outsideSet) outside.push_back(cellIndex.at(c));
        const auto& cc=mesh.cellCentres();
        const auto& vol=mesh.cellVolumes();
        for (const FaceData& f:faces)
        {
            for (label c:{f.owner,f.neighbour})
                if (c>=0 && !cellIndex.count(c))
                {
                    auto updated=currentCells.find(c);
                    halo[c]=updated==currentCells.end()
                        ? std::make_pair(cc[c],vol[c]) : updated->second;
                }
        }
    }
    std::pair<vector,scalar> cellGeometry(label id,const State& state) const
    {
        auto it=cellIndex.find(id);
        return it==cellIndex.end() ? halo.at(id)
            : std::make_pair(state.cc[it->second],state.volume[it->second]);
    }
    State evaluate(const pointField& p, bool installed=false) const
    {
        State s;
        s.sf.resize(faces.size());s.fc.resize(faces.size());
        s.cc.resize(cells.size());s.volume.resize(cells.size());
        s.determinant.resize(cells.size());s.faceQuality.resize(faces.size());
        const vectorField* installedSf=nullptr;
        const vectorField* installedFc=nullptr;
        const vectorField* installedCc=nullptr;
        const scalarField* installedV=nullptr;
        if (installed)
        {
            installedSf=&mesh.faceAreas();installedFc=&mesh.faceCentres();
            installedCc=&mesh.cellCentres();installedV=&mesh.cellVolumes();
        }
        vectorField calculatedSf,calculatedFc;
        if (!installed) primitiveMeshTools::makeFaceCentresAndAreas
            (compactFaces,p,calculatedFc,calculatedSf);
        for (label i=0;i<label(faces.size());++i)
        {
            const FaceData& f=faces[i];
            s.sf[i]=installed ? (*installedSf)[f.id] : calculatedSf[i];
            s.fc[i]=installed ? (*installedFc)[f.id] : calculatedFc[i];
            if (!finiteVector(s.sf[i]) || !finiteVector(s.fc[i])
             || mag(s.sf[i])<=VSMALL) s.valid=false;
        }
        for (label i=0;i<label(cells.size());++i)
        {
            const CellData& c=cells[i];
            vector estimate(Zero);
            for (label f:c.faces) estimate+=s.fc[f];
            estimate/=scalar(c.faces.size());
            scalar v3=0; vector moment(Zero);
            // cells() has the same owner-then-neighbour accumulation order
            // as installed makeCellCentresAndVols.
            for (label f:c.faces)
            {
                const scalar pv=(faces[f].owner==c.id)
                    ? (s.sf[f] & (s.fc[f]-estimate))
                    : (s.sf[f] & (estimate-s.fc[f]));
                const vector pc=(3.0/4.0)*s.fc[f]+(1.0/4.0)*estimate;
                moment+=pv*pc;v3+=pv;
            }
            s.cc[i]=installed ? (*installedCc)[c.id]
                : (mag(v3)>VSMALL ? moment/v3 : estimate);
            s.volume[i]=installed ? (*installedV)[c.id] : v3*(1.0/3.0);
            scalar avg=0;label n=0;
            for (label f:c.faces) if (faces[f].neighbour>=0 || faces[f].coupled)
            { avg+=mag(s.sf[f]);++n; }
            scalar d=0;
            if (n && avg>=ROOTVSMALL)
            {
                avg/=n;symmTensor tensor(Zero);
                for (label f:c.faces) if (faces[f].neighbour>=0 || faces[f].coupled)
                    tensor+=sqr(s.sf[f]/avg);
                d=mag(det(tensor))/8.0;
            }
            s.determinant[i]=d;
            if (!finiteVector(s.cc[i]) || !finiteScalar(s.volume[i])
             || s.volume[i]<=VSMALL || !finiteScalar(d)) s.valid=false;
        }
        for (label i=0;i<label(faces.size());++i)
        {
            const FaceData& f=faces[i];
            // A coupled outside face is untouched. Its remote state is
            // unknown here and is never substituted with a local estimate.
            if (f.coupled) { s.faceQuality[i]={{1,1,0,0}}; continue; }
            const auto own=cellGeometry(f.owner,s);
            vector delta(Zero);
            scalar w=1,r=1,n=0;
            if (f.neighbour>=0)
            {
                const auto nei=cellGeometry(f.neighbour,s);
                delta=nei.first-own.first;
                const scalar a=mag(s.sf[i] & (s.fc[i]-own.first));
                const scalar b=mag(s.sf[i] & (nei.first-s.fc[i]));
                w=min(a,b)/(a+b+VSMALL);
                r=min(own.second,nei.second)/(max(own.second,nei.second)+VSMALL);
                scalar cosine=(delta & s.sf[i])/(mag(delta)*mag(s.sf[i])+ROOTVSMALL);
                n=std::acos(max(scalar(-1),min(scalar(1),cosine)))*180.0/constant::mathematical::pi;
            }
            else
            {
                const vector normal=s.sf[i]/mag(s.sf[i]);
                delta=normal*(normal & (s.fc[i]-own.first));
            }
            const vector cpf=s.fc[i]-own.first;
            const vector skew=cpf-((s.sf[i]&cpf)/((s.sf[i]&delta)+ROOTVSMALL))*delta;
            const vector shat=skew/(mag(skew)+ROOTVSMALL);
            scalar fd=(f.neighbour>=0 ? .2 : .4)*mag(delta)+ROOTVSMALL;
            for (label v:f.vertices) fd=max(fd,mag(shat&(p[v]-s.fc[i])));
            const scalar sk=mag(skew)/fd;
            s.faceQuality[i]={{w,r,sk,n}};
            for (scalar value:s.faceQuality[i]) if (!finiteScalar(value)) s.valid=false;
        }
        return s;
    }
    pointField fromGlobal(const pointField& p) const
    {
        pointField out(pointIDs.size());
        for (label i=0;i<label(pointIDs.size());++i) out[i]=p[pointIDs[i]];
        return out;
    }
};

inline bool qualityLineage
(
    const Stencil& stencil,const State& before,const State& after,scalar ceiling
)
{
    if (!before.valid || !after.valid || !finiteScalar(ceiling)) return false;
    auto compare=[](const std::vector<scalar>& a,const std::vector<scalar>& b,scalar limit,bool upper)
    {
        return dcCartFiniteRepairKernel::preservesHardRoles(a,b,limit,upper,scalar(VSMALL));
    };
    // Include outside determinants as roles; unchanged geometry is additionally
    // checked bitwise below rather than inferred from the quality verdict.
    if (!compare(before.determinant,after.determinant,.001,false)) return false;
    for (label k=0;k<3;++k)
    {
        std::vector<scalar> a,b;
        for (label f=0;f<label(stencil.faces.size());++f) if (!stencil.faces[f].coupled)
        { a.push_back(before.faceQuality[f][k]);b.push_back(after.faceQuality[f][k]); }
        if (!compare(a,b,k==0 ? .05 : k==1 ? .01 : 4,k==2)) return false;
    }
    label oldN=0,newN=0;
    for (label f=0;f<label(stencil.faces.size());++f) if (!stencil.faces[f].coupled)
    {
        oldN+=before.faceQuality[f][3]>70;newN+=after.faceQuality[f][3]>70;
        if (after.faceQuality[f][3]>ceiling) return false;
    }
    if (newN>oldN) return false;
    for (label c:stencil.outside)
    {
        if (!sameVector(before.cc[c],after.cc[c])
         || !sameScalar(before.volume[c],after.volume[c])) return false;
        for (label f:stencil.cells[c].faces)
            if (!sameVector(before.sf[f],after.sf[f])
             || !sameVector(before.fc[f],after.fc[f])) return false;
    }
    return true;
}

inline bool geometricGuard
(
    const Stencil& stencil,const State& before,const State& after,
    const pointField& points
)
{
    if (!before.valid || !after.valid) return false;
    scalar oldVolume=0,newVolume=0;vector oldMoment(Zero),newMoment(Zero);
    const scalar planeTol=1e-10*stencil.scale;
    for (label ci:stencil.core)
    {
        const CellData& c=stencil.cells[ci];
        oldVolume+=before.volume[ci];newVolume+=after.volume[ci];
        oldMoment+=before.volume[ci]*before.cc[ci];
        newMoment+=after.volume[ci]*after.cc[ci];
        vector closure(Zero);scalar area=0;
        for (label f:c.faces)
        {
            const scalar sense=stencil.faces[f].owner==c.id ? 1 : -1;
            const vector normal=sense*after.sf[f]/mag(after.sf[f]);
            closure+=sense*after.sf[f];area+=mag(after.sf[f]);
            if ((normal & (after.cc[ci]-after.fc[f]))>=0) return false;
            for (label g:c.faces) for (label vertex:stencil.faces[g].vertices)
                if ((normal & (points[vertex]-after.fc[f]))>planeTol) return false;
            for (label g:c.faces) if (g!=f)
            {
                const vector delta=after.fc[g]-after.fc[f];
                if ((normal & (delta/max(mag(delta),VSMALL)))>-1e-6) return false;
            }
            // Sufficient convex/planar face guard; no point projection.
            const face& poly=stencil.faces[f].vertices;
            for (label v:poly)
                if (mag(normal & (points[v]-after.fc[f]))>planeTol) return false;
            forAll(poly,e)
            {
                const vector edge=points[poly.nextLabel(e)]-points[poly[e]];
                for (label v:poly)
                    if (((edge ^ (points[v]-points[poly[e]])) & after.sf[f])
                        < -planeTol*mag(after.sf[f])*max(mag(edge),ROOTVSMALL))
                        return false;
                const scalar quality=tetPointRef
                (
                    points[poly[e]],points[poly.nextLabel(e)],
                    after.fc[f],after.cc[ci]
                ).quality();
                if (!finiteScalar(quality) || (sense>0 ? quality>-polyMeshTetDecomposition::minTetQuality
                    : quality<polyMeshTetDecomposition::minTetQuality))
                    return false;
            }
        }
        if (mag(closure)>1e-10*max(area,VSMALL)) return false;
    }
    // Official vertex-fan requirement, using one shared anchor on both sides.
    for (label f=0;f<label(stencil.faces.size());++f)
    {
        const FaceData& fd=stencil.faces[f];
        bool touchesCore=false;
        const auto owner=stencil.cellIndex.find(fd.owner);
        const auto neighbour=stencil.cellIndex.find(fd.neighbour);
        if (owner!=stencil.cellIndex.end())
            touchesCore=std::find(stencil.core.begin(),stencil.core.end(),owner->second)!=stencil.core.end();
        if (neighbour!=stencil.cellIndex.end())
            touchesCore=touchesCore || std::find(stencil.core.begin(),stencil.core.end(),neighbour->second)!=stencil.core.end();
        if (!touchesCore || fd.coupled) continue;
        const face& poly=fd.vertices;bool shared=false;
        forAll(poly,anchor)
        {
            bool good=true;
            const auto own=stencil.cellGeometry(fd.owner,after);
            for (label j=1;j<poly.size()-1;++j)
            {
                label a=poly[(anchor+j)%poly.size()],b=poly[(anchor+j+1)%poly.size()];
                scalar q=tetPointRef(own.first,points[poly[anchor]],points[a],points[b]).quality();
                if (!finiteScalar(q) || q<=polyMeshTetDecomposition::minTetQuality) { good=false;break; }
                if (fd.neighbour>=0)
                {
                    const auto nei=stencil.cellGeometry(fd.neighbour,after);
                    q=tetPointRef(nei.first,points[poly[anchor]],points[b],points[a]).quality();
                    if (!finiteScalar(q) || q<=polyMeshTetDecomposition::minTetQuality) { good=false;break; }
                }
            }
            if (good) { shared=true;break; }
        }
        if (!shared) return false;
    }
    return mag(newVolume-oldVolume)<=1e-10*max(mag(oldVolume),VSMALL)
        && mag(newMoment-oldMoment)<=1e-10*max
        (
            mag(oldMoment),mag(oldVolume)*stencil.scale
        );
}

inline dcCartFiniteRepairKernel::Mode selectMode
(
    const polyMesh& mesh,const pointField& points,label target,
    const std::vector<unsigned char>& protectedFace,
    const std::vector<unsigned char>& protectedPoint
)
{
    // Only the complete star of the three target vertices is copied for the
    // pure selector; this does not allocate a second global face graph.
    const auto& pf=mesh.pointFaces();
    std::set<label> fids,pids;
    for (label p:mesh.faces()[target])
        for (label f:pf[p])
        {
            fids.insert(f);
            if (fids.size()>512) return dcCartFiniteRepairKernel::Mode{};
        }
    for (label f:fids) for (label p:mesh.faces()[f]) pids.insert(p);
    std::map<label,int> fm,pm;
    std::vector<label> inverse;
    std::vector<dcCartFiniteRepairKernel::XYZ> xyz;
    std::vector<dcCartFiniteRepairKernel::Face> faces;
    std::vector<std::vector<int>> graph;
    for (label p:pids)
    {
        pm[p]=xyz.size();inverse.push_back(p);
        xyz.push_back({{points[p].x(),points[p].y(),points[p].z()}});
        graph.emplace_back();
    }
    for (label f:fids)
    {
        const int i=faces.size();fm[f]=i;
        dcCartFiniteRepairKernel::Face data;
        data.protectedFace=protectedFace[f];
        for (label p:mesh.faces()[f])
        {
            data.points.push_back(pm.at(p));graph[pm.at(p)].push_back(i);
            // Point-zone protection is applied to the queried point star below.
        }
        faces.push_back(data);
    }
    // A star for any non-target vertex is incomplete in this compact graph.
    // The selector only queries target vertices; explicitly fence all others.
    std::set<label> targetVertices;
    for (label p:mesh.faces()[target]) targetVertices.insert(p);
    for (label p:pids) if (!targetVertices.count(p) || protectedPoint[p]) graph[pm.at(p)].clear();
    auto mode=dcCartFiniteRepairKernel::select(xyz,faces,graph,fm.at(target));
    if (mode.dimension)
    {
        mode.pivot=inverse[mode.pivot];
        if (mode.pairA>=0) mode.pairA=inverse[mode.pairA];
        if (mode.pairB>=0) mode.pairB=inverse[mode.pairB];
    }
    return mode;
}

inline bool pointClearance
(
    const pointField& global,const std::vector<label>& moving,
    const pointField& trial,const Stencil& stencil,
    const std::function<bool()>& available
)
{
    std::set<label> moved(moving.begin(),moving.end());
    for (label p:moving)
    {
        const point& q=trial[stencil.pointIndex.at(p)];
        if (!finiteVector(q)) return false;
        for (label other=0;other<global.size();++other)
        {
            if ((other&4095)==0 && !available()) return false;
            if (moved.count(other)) continue;
            if (magSqr(q-global[other])<=sqr(scalar(3e-6))) return false;
        }
    }
    for (std::size_t i=0;i<moving.size();++i)
        for (std::size_t j=i+1;j<moving.size();++j)
            if (magSqr(trial[stencil.pointIndex.at(moving[i])]
                      -trial[stencil.pointIndex.at(moving[j])])<=sqr(scalar(3e-6)))
                return false;
    return true;
}

inline void install(polyMesh& mesh,const pointField& points)
{
    pointIOField field
    (
        IOobject
        (
            "dcCartFinitePointRepairPoints",mesh.pointsInstance(),mesh,
            IOobject::NO_READ,IOobject::NO_WRITE,false
        ),
        points
    );
    autoPtr<labelIOList> emptyTet;
    mesh.clearTetBasePtIs();
    mesh.updateGeomPoints(std::move(field),emptyTet);
    (void)mesh.faceAreas();(void)mesh.faceCentres();
    (void)mesh.cellCentres();(void)mesh.cellVolumes();
    // All ranks call together; tetBasePtIs can exchange boundary information.
    (void)mesh.tetBasePtIs();
}

inline Result apply
(
    polyMesh& mesh,const std::function<scalar()>& remainingStageBudget
)
{
    // Clock starts before any graph, geometry cache or census.
    const auto start=std::chrono::steady_clock::now();
    auto seconds=[&]()
    {
        return scalar(std::chrono::duration<double>
            (std::chrono::steady_clock::now()-start).count());
    };
    auto allowance=[&]()
    {
        const scalar external=remainingStageBudget ? remainingStageBudget() : 0;
        return finiteScalar(external) && external>0
            ? min(max(scalar(0),20-seconds()),external) : scalar(0);
    };
    // A reserve controls admission only; mandatory collective refresh/rollback
    // cannot be interrupted. Total time is measured and overrun rejects changes.
    auto available=[&]() { return allowance()>3; };
    Result result;

    // This is an unconditional initial installed boundary exchange, followed
    // by one max reduction: the warning ceiling includes real coupled neighbours.
    scalar ceiling=0;
    {
        const tmp<scalarField> orthogonality=polyMeshTools::faceOrthogonality
            (mesh,mesh.faceAreas(),mesh.cellCentres());
        for (scalar cosine:orthogonality())
        {
            if (!finiteScalar(cosine)) { ceiling=GREAT; break; }
            ceiling=max(ceiling,std::acos(max(scalar(-1),min(scalar(1),cosine)))
                *180.0/constant::mathematical::pi);
        }
    }
    reduce(ceiling,maxOp<scalar>());
    // Non-finite input is unsupported, never treated as a permissive ceiling.
    label validCeiling=finiteScalar(ceiling) && ceiling<180;
    reduce(validCeiling,minOp<label>());
    // Earliest installed tet cache is forced collectively, not inside a trial.
    (void)mesh.tetBasePtIs();

    pointField originalPoints(mesh.points());
    pointField workingPoints(originalPoints);
    std::set<label> changedPoints;
    std::map<label,std::pair<vector,scalar>> currentCells;
    std::vector<label> targets;
    std::vector<unsigned char> protectedFace,protectedPoint;
    bool localReady=validCeiling && available();
    const Vector<label> dimensions=mesh.geometricD();
    for (direction k=0;k<3;++k) if (dimensions[k]!=1) localReady=false;

    if (localReady)
    {
        forAll(originalPoints,p)
        {
            if ((p&4095)==0 && !available()) { localReady=false;break; }
            if (!finiteVector(originalPoints[p])) { localReady=false;break; }
        }
    }
    if (localReady)
    {
        (void)mesh.cells();
        if (available()) (void)mesh.pointFaces(); else localReady=false;
    }
    if (localReady && available())
    {
        protectedFace.assign(mesh.nFaces(),0);
        protectedPoint.assign(mesh.nPoints(),0);
        for (label f=mesh.nInternalFaces();f<mesh.nFaces();++f)
        {
            protectedFace[f]=1;
            // Face boundary protects incident points regardless of patch kind.
            for (label p:mesh.faces()[f]) protectedPoint[p]=1;
        }
        for (const faceZone& zone:mesh.faceZones())
            for (label f:zone) protectedFace[f]=1;
        for (const pointZone& zone:mesh.pointZones())
            for (label p:zone) protectedPoint[p]=1;
        for (const cellZone& zone:mesh.cellZones())
            for (label c:zone) for (label f:mesh.cells()[c])
                for (label p:mesh.faces()[f]) protectedPoint[p]=1;
        const auto& sf=mesh.faceAreas();
        const auto& fc=mesh.faceCentres();
        const auto& cc=mesh.cellCentres();
        const auto& vol=mesh.cellVolumes();
        const auto& own=mesh.faceOwner();const auto& nei=mesh.faceNeighbour();
        for (label f=0;f<mesh.nInternalFaces();++f)
        {
            if ((f&4095)==0 && !available()) { localReady=false;break; }
            if (protectedFace[f] || mesh.faces()[f].size()!=3) continue;
            const scalar ratio=min(vol[own[f]],vol[nei[f]])
                /(max(vol[own[f]],vol[nei[f]])+VSMALL);
            if (finiteScalar(ratio) && ratio<.01) targets.push_back(f);
        }
        // Stable geometry order has no rank-local label tie-break. Duplicate
        // geometric targets are refused instead of choosing an arbitrary ID.
        auto key=[&](label f)
        {
            std::array<scalar,9> out;std::array<vector,3> vertices;
            for (label j=0;j<3;++j) vertices[j]=workingPoints[mesh.faces()[f][j]];
            auto lex=[](const vector& a,const vector& b)
            {
                for (direction k=0;k<3;++k)
                    if (a[k]!=b[k]) return a[k]<b[k];
                return false;
            };
            std::sort(vertices.begin(),vertices.end(),lex);
            for (label j=0;j<3;++j) for (direction k=0;k<3;++k) out[3*j+k]=vertices[j][k];
            return out;
        };
        std::sort(targets.begin(),targets.end(),[&](label a,label b){return key(a)<key(b);});
        std::vector<label> unique;
        for (std::size_t i=0;i<targets.size();++i)
        {
            const auto current=key(targets[i]);
            if ((i && key(targets[i-1])==current)
             || (i+1<targets.size() && key(targets[i+1])==current))
            { ++result.unsupported; continue; }
            unique.push_back(targets[i]);
        }
        targets.swap(unique);
        // The rank-local component cap is separate from the evaluation cap.
        if (targets.size()>8) { result.unsupported+=targets.size()-8;targets.resize(8); }
    }
    result.setupSeconds=seconds();
    const scalar proposalStart=seconds();
    for (label target:targets)
    {
        if (!localReady || !available()) { ++result.budgetStopped;break; }
        const auto mode=selectMode(mesh,workingPoints,target,protectedFace,protectedPoint);
        if (!mode.dimension) { ++result.unsupported;continue; }
        std::vector<label> moving{mode.pivot};
        if (mode.dimension==4) { moving.push_back(mode.pairA);moving.push_back(mode.pairB); }
        Stencil stencil(mesh,workingPoints,moving,currentCells);
        if (!available()) { ++result.budgetStopped;break; }
        if (!stencil.eligible) { ++result.unsupported;continue; }
        const pointField initial(stencil.original);
        const State before=stencil.evaluate(initial);
        bool baselineMatches=true;
        if (changedPoints.empty())
        {
            const State actual=stencil.evaluate(initial,true);
            for (label f=0;f<label(stencil.faces.size());++f)
                if (!sameVector(before.sf[f],actual.sf[f])
                 || !sameVector(before.fc[f],actual.fc[f])) baselineMatches=false;
            for (label c=0;c<label(stencil.cells.size());++c)
                if (!sameVector(before.cc[c],actual.cc[c])
                 || !sameScalar(before.volume[c],actual.volume[c])) baselineMatches=false;
        }
        if (!before.valid || !baselineMatches) { ++result.unsupported;continue; }
        const label targetIndex=stencil.faceIndex.at(target);
        if (before.faceQuality[targetIndex][1]>=.01) continue;
        auto coordinates=[&](const dcCartFiniteRepairKernel::Row& q,scalar fraction=1)
        {
            pointField p(initial);
            for (direction k=0;k<3;++k)
                p[stencil.pointIndex.at(mode.pivot)][k]+=fraction*q[k]*stencil.scale;
            if (mode.dimension==4)
            {
                const scalar delta=fraction*q[3]*stencil.scale;
                p[stencil.pointIndex.at(mode.pairA)][mode.axis]+=delta;
                p[stencil.pointIndex.at(mode.pairB)][mode.axis]+=delta;
            }
            return p;
        };
        std::set<label> activeSet;
        for (label c:stencil.core) for (label f:stencil.cells[c].faces) activeSet.insert(f);
        const std::vector<label> activeFaces(activeSet.begin(),activeSet.end());
        scalar minD=.001,minW=.05,minR=.01,maxS=4;
        for (label c:stencil.core) minD=min(minD,before.determinant[c]);
        for (label f:activeFaces) if (!stencil.faces[f].coupled)
        {
            minW=min(minW,before.faceQuality[f][0]);
            minR=min(minR,before.faceQuality[f][1]);
            maxS=max(maxS,before.faceQuality[f][2]);
        }
        auto evaluate=[&](const dcCartFiniteRepairKernel::Row& q)
        {
            dcCartFiniteRepairKernel::Evaluation value;
            if (q.size()!=mode.dimension || !dcCartFiniteRepairKernel::finite(q)) return value;
            const State next=stencil.evaluate(coordinates(q));
            if (!next.valid) return value;
            value.objective=-next.faceQuality[targetIndex][1]/.01;
            for (label c:stencil.core)
            {
                const scalar bound=before.determinant[c]>=.001 ? .001 : minD;
                value.constraints.push_back((next.determinant[c]-bound)/.001);
            }
            for (label k=0;k<4;++k)
                for (label f:activeFaces) if (!stencil.faces[f].coupled)
                {
                    const scalar old=before.faceQuality[f][k],v=next.faceQuality[f][k];
                    const scalar limit=k==0 ? .05 : k==1 ? .01 : k==2 ? 4 : 70;
                    const scalar bound=k==0 ? (old>=limit ? limit : minW)
                        : k==1 ? (old>=limit ? limit : minR)
                        : k==2 ? (old<=limit ? limit : maxS) : ceiling;
                    value.constraints.push_back(k<2 ? (v-bound)/limit : (bound-v)/limit);
                }
            for (label c:stencil.core) value.constraints.push_back(next.determinant[c]/.001);
            for (label c:stencil.core)
                value.constraints.push_back
                (
                    (next.volume[c]-1e-5*before.volume[c])/max(before.volume[c],VSMALL)
                );
            value.valid=finiteScalar(value.objective)
                && dcCartFiniteRepairKernel::finite(value.constraints);
            return value;
        };
        ++result.proposed;
        const auto search=dcCartFiniteRepairKernel::propose(mode.dimension,evaluate,available);
        result.requests+=search.requests;
        if (search.stoppedByBudget) ++result.budgetStopped;
        bool accepted=false;
        // Actual role/geometry guards, distinct from approximate proposal rows.
        for (scalar fraction:{scalar(1),scalar(.95),scalar(.9),scalar(.5)})
        {
            if (!search.improved || !available()) break;
            const pointField candidate=coordinates(search.parameters,fraction);
            const State after=stencil.evaluate(candidate);
            if (after.faceQuality[targetIndex][1]<.01
             || !qualityLineage(stencil,before,after,ceiling)
             || !geometricGuard(stencil,before,after,candidate)
             || !pointClearance(workingPoints,moving,candidate,stencil,available)) continue;
            for (label p:moving)
            { workingPoints[p]=candidate[stencil.pointIndex.at(p)];changedPoints.insert(p); }
            for (label c:stencil.core)
                currentCells[stencil.cells[c].id]={after.cc[c],after.volume[c]};
            ++result.committed;accepted=true;
            Pout<< "dcCart finite point proposal: target=" << target
                << " dimension=" << mode.dimension << " pivot=" << mode.pivot
                << " pairA=" << mode.pairA << " pairB=" << mode.pairB
                << " axis=" << mode.axis << " fraction=" << fraction
                << " oldRatio=" << before.faceQuality[targetIndex][1]
                << " proposedRatio=" << after.faceQuality[targetIndex][1]
                << " core=" << stencil.core.size() << " outside=" << stencil.outside.size()
                << " faces=" << stencil.faces.size() << " points=" << stencil.pointIDs.size()
                << " requests=" << search.requests << nl;
            break;
        }
        if (!accepted) ++result.refused;
    }
    result.proposalSeconds=seconds()-proposalStart;
    // Every local stop reaches these collectives. No trial has communicated.
    label changed=!changedPoints.empty();
    reduce(changed,maxOp<label>());
    if (changed)
    {
        // Freeze the complete joint baseline while original installed caches
        // still exist. Components that overlap are assessed together here.
        const std::vector<label> moving(changedPoints.begin(),changedPoints.end());
        Stencil joint(mesh,originalPoints,moving);
        State before;
        std::vector<label> earliestTetBases;
        if (joint.eligible)
        {
            before=joint.evaluate(joint.fromGlobal(originalPoints),true);
            const auto& bases=mesh.tetBasePtIs();
            for (label f:joint.faceIDs) earliestTetBases.push_back(bases[f]);
        }
        const scalar installedStart=seconds();
        install(mesh,workingPoints); // All ranks, including zero-local-change.
        result.installedSeconds=seconds()-installedStart;
        const scalar guardStart=seconds();
        label failure=0;
        if (!moving.empty())
        {
            if (!joint.eligible || !before.valid) failure=1;
            else
            {
                const pointField actual=joint.fromGlobal(mesh.points());
                const State after=joint.evaluate(actual,true);
                if (!qualityLineage(joint,before,after,ceiling)
                 || !geometricGuard(joint,before,after,actual)) failure=1;
                const auto& bases=mesh.tetBasePtIs();
                for (label f=0;f<label(joint.faces.size());++f)
                {
                    const label base=bases[joint.faces[f].id];
                    if (earliestTetBases[f]>=0 && (base<0
                     || base>=joint.faces[f].vertices.size())) failure=1;
                }
            }
        }
        // Topology is never changed and all moving points had complete
        // internal, zone-free stars. Verify every other stored point bitwise.
        for (label p=0;p<mesh.nPoints();++p)
            if (!changedPoints.count(p) && !sameVector(originalPoints[p],mesh.points()[p]))
            { failure=1;break; }
        if (!finiteScalar(seconds()) || seconds()>20
         || !remainingStageBudget || !finiteScalar(remainingStageBudget())
         || remainingStageBudget()<=0) failure=1;
        result.guardSeconds=seconds()-guardStart;
        reduce(failure,maxOp<label>());
        if (failure)
        {
            const scalar rollbackStart=seconds();
            install(mesh,originalPoints); // Same refresh on every rank.
            result.rollbackSeconds=seconds()-rollbackStart;
            result.finalRolledBack=result.committed;result.committed=0;
        }
    }
    result.elapsedSeconds=seconds();
    Pout<< "dcCart finite point repair: proposed=" << result.proposed
        << " committed=" << result.committed << " refused=" << result.refused
        << " unsupported=" << result.unsupported << " requests=" << result.requests
        << " budgetStopped=" << result.budgetStopped
        << " rolledBack=" << result.finalRolledBack
        << " setup=" << result.setupSeconds << " proposal=" << result.proposalSeconds
        << " installed=" << result.installedSeconds << " guard=" << result.guardSeconds
        << " rollback=" << result.rollbackSeconds << " total=" << result.elapsedSeconds
        << " cap=20 componentCap=8 requestCap=256" << nl;
    return result;
}
} // namespace dcCartFinitePointRepair
} // namespace Foam
#endif // production adapter
#endif // dcCartFinitePointRepair_H
