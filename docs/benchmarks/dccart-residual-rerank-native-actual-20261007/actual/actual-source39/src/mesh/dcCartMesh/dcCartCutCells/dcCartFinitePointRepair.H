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
#include <cstdlib>
#include <cstdint>
#include <functional>
#include <limits>
#include <map>
#include <set>
#include <sstream>
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
enum class ObjectiveKind { Ratio, DWeightCount };
// Private policy gate: never expands parallel or Ratio searches.
inline bool widerDWeightSearchEnabled(const char* flag,bool parallel)
{
    return !parallel && flag && std::strcmp(flag,"1")==0;
}

struct Evaluation
{
    double objective=0;
    Row constraints;
    bool valid=false;
    double hardDW=-1; // Optional exact nonnegative count; unset keeps R behavior.
};
inline bool validHardDW(double n)
{
    return std::isfinite(n) && n>=0 && n<=std::numeric_limits<unsigned>::max()
        && std::floor(n)==n;
}
struct Search
{
    Row parameters, bestHardParameters;
    double bestHardDW=-1;
    bool bestHardImproved=false;
    unsigned requests=0, iterations=0, projectionUpdates=0;
    unsigned restorationAttempts=0,restorationRequests=0; // Private pure-kernel policy only.
    bool improved=false, stoppedByBudget=false;
    bool stoppedByRequestCap=false,stoppedByTimeBudget=false;
};
inline Search propose
(
    unsigned dimension,
    const std::function<Evaluation(const Row&)>& evaluate,
    const std::function<bool()>& available,
    ObjectiveKind objectiveKind=ObjectiveKind::Ratio,
    bool widerDWeightSearch=false,
    bool nonlinearFeasibilityTail=false // Default OFF; no production caller enables this.
)
{
    Search out; out.parameters.assign(dimension,0);
    if ((objectiveKind==ObjectiveKind::Ratio && dimension!=3 && dimension!=4)
     || (objectiveKind==ObjectiveKind::DWeightCount && dimension!=3 && dimension!=9)) return out;
    auto request=[&](const Row& q, Evaluation& e)
    {
        if (out.requests>=256)
        { out.stoppedByBudget=true;out.stoppedByRequestCap=true;return false; }
        if (!available())
        { out.stoppedByBudget=true;out.stoppedByTimeBudget=true;return false; }
        ++out.requests; e=evaluate(q);
        return e.valid && std::isfinite(e.objective)
            && !e.constraints.empty() && finite(e.constraints);
    };
    const bool widerDW=widerDWeightSearch && objectiveKind==ObjectiveKind::DWeightCount;
    const bool restoreDW=widerDW && nonlinearFeasibilityTail;
    const double trustBound=widerDW ? .2 : .08;
    Row q(dimension,0); Evaluation current;
    if (!request(q,current)) return out;
    const double initialHardDW=current.hardDW;
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
        bool restorationKnown=false;Row restorationQ;Evaluation restorationValue;
        for (double radius:{.004,.002,.001,.0005})
        {
            if (widerDW) radius*=5; // DW only: .02/.01/.005/.0025.
            Row desired=grad;
            for (double& x:desired) x*=-radius/n;
            Matrix a=jac; Row b=current.constraints;
            for (unsigned k=0; k<dimension; ++k)
            {
                Row row(dimension,0); row[k]=1;
                a.push_back(row); b.push_back(q[k]+trustBound);
            }
            for (unsigned k=0; k<dimension; ++k)
            {
                Row row(dimension,0); row[k]=-1;
                a.push_back(row); b.push_back(trustBound-q[k]);
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
                // Only own an already-evaluated, descending but nonlinear-infeasible trial.
                // No extra callbacks or allocations on the default-OFF path.
                if (restoreDW
                 && *std::min_element(next.constraints.begin(),next.constraints.end())<-2e-9
                 && next.objective<current.objective-1e-8
                 && (!restorationKnown || next.objective<restorationValue.objective))
                { restorationQ=trial;restorationValue=next;restorationKnown=true; }
                if (*std::min_element(next.constraints.begin(),next.constraints.end())>=-2e-9
                 && next.objective<current.objective-1e-8)
                {
                    q=trial; current=next; accepted=true;
                    out.parameters=q; out.improved=true;
                    // Reuse the already-evaluated accepted feasible iterate.
                    // Soft trajectory and request accounting stay unchanged.
                    if (objectiveKind==ObjectiveKind::DWeightCount
                     && validHardDW(initialHardDW) && validHardDW(current.hardDW)
                     && current.hardDW<initialHardDW
                     && (!out.bestHardImproved || current.hardDW<out.bestHardDW))
                    {
                        out.bestHardParameters=q;out.bestHardDW=current.hardDW;
                        out.bestHardImproved=true;
                    }
                    break;
                }
            }
            if (!valid || accepted) break;
        }
        // Preserve the complete original radius/fraction search first. One fixed
        // tail per proposal may correct curvature residuals using this iteration's Jacobian.
        // All evaluations use request(), so the original 256/32/time caps remain.
        if (restoreDW && valid && !accepted && restorationKnown
         && out.restorationAttempts==0)
        {
            ++out.restorationAttempts;
            for (unsigned correction=0;correction<2;++correction)
            {
                Matrix a=jac;Row b=restorationValue.constraints;
                for (unsigned k=0;k<dimension;++k)
                {
                    Row row(dimension,0);row[k]=1;
                    a.push_back(row);b.push_back(restorationQ[k]+trustBound);
                }
                for (unsigned k=0;k<dimension;++k)
                {
                    Row row(dimension,0);row[k]=-1;
                    a.push_back(row);b.push_back(trustBound-restorationQ[k]);
                }
                const Projection projected=project(Row(dimension,0),a,b);
                out.projectionUpdates+=projected.updates;
                if (!projected.feasible || !finite(projected.step)
                 || dot(projected.step,projected.step)==0) break;
                Row corrected=restorationQ;
                for (unsigned k=0;k<dimension;++k) corrected[k]+=projected.step[k];
                Evaluation next;const unsigned requestsBefore=out.requests;
                const bool requested=request(corrected,next);
                out.restorationRequests+=out.requests-requestsBefore;
                if (!requested || next.constraints.size()!=current.constraints.size())
                { valid=false;break; }
                if (*std::min_element(next.constraints.begin(),next.constraints.end())>=-2e-9
                 && next.objective<current.objective-1e-8)
                {
                    q=corrected;current=next;accepted=true;
                    out.parameters=q;out.improved=true;
                    if (validHardDW(initialHardDW) && validHardDW(current.hardDW)
                     && current.hardDW<initialHardDW
                     && (!out.bestHardImproved || current.hardDW<out.bestHardDW))
                    {
                        out.bestHardParameters=q;out.bestHardDW=current.hardDW;
                        out.bestHardImproved=true;
                    }
                    break;
                }
                restorationQ=corrected;restorationValue=next;
            }
        }
        ++out.iterations;
        if (!valid || !accepted
         || (objectiveKind==ObjectiveKind::Ratio && -current.objective*.01>=.0105)) break;
    }
    return out;
}


// Default-off serial read-only records; labels are polyMesh diagnostics only.
enum class DWVeto : unsigned { None, Invalid, Ratio, Progress, Lineage, Geometry, Clearance, Prefix };
template<class I,class R,class H,class Q,class G,class C,class P>
inline DWVeto firstDWVeto(unsigned& called,I i,R r,H h,Q q,G g,C c,P p)
{
    called=0;
    called|=1; if(i())return DWVeto::Invalid;
    called|=2; if(r())return DWVeto::Ratio;
    called|=4; if(h())return DWVeto::Progress;
    called|=8; if(q())return DWVeto::Lineage;
    called|=16;if(g())return DWVeto::Geometry;
    called|=32;if(c())return DWVeto::Clearance;
    called|=64;if(p())return DWVeto::Prefix;
    return DWVeto::None;
}
inline std::uint64_t observationBits(double x) noexcept
{ std::uint64_t b=0;static_assert(sizeof(b)==sizeof(x),"IEEE64");std::memcpy(&b,&x,sizeof(b));return b; }
struct DWObservedSummary
{ unsigned valid,cells,faces,bad[5];std::uint64_t extreme[5];std::int64_t ids[5]; };
struct DWObservedProposal
{
    unsigned dimension,n,evalCalls,evalValid,firstInvalid,requests,iterations,projectionUpdates;
    unsigned improved,bestHardImproved,requestCap,timeCap,breakReason;
    unsigned restorationAttempts,restorationRequests; // Two scalars, same record slots/caps.
    std::int64_t moving[3];std::uint64_t oldPoint[9],firstObjective,lastObjective,firstHard,lastHard,parameters[9],bestParameters[9];
    DWObservedSummary before;
};
struct DWObservedFraction
{
    unsigned proposal,checkpoint,called,veto,hardCalled;
    std::int64_t oldHard,nextHard;std::uint64_t fraction,point[9];DWObservedSummary after;
};
struct DWVetoObservation
{
    bool enabled=false,incomplete=false;unsigned seen=0,count=0,fractionSeen=0,fractionCount=0;
    DWObservedProposal proposals[16];DWObservedFraction fractions[128];
    int open(const std::int64_t* ids,unsigned n,unsigned dim,const std::uint64_t* point,const DWObservedSummary& before) noexcept
    {
        if(!enabled)return -1;++seen;
        if(count==16||n==0||n>3||dim>9){incomplete=true;return -1;}
        unsigned at=count++;auto& p=proposals[at];p={};p.dimension=dim;p.n=n;p.before=before;
        for(unsigned j=0;j<n;++j)p.moving[j]=ids[j];for(unsigned j=0;j<3*n;++j)p.oldPoint[j]=point[j];
        return int(at);
    }
    void evaluation(int at,const Evaluation& e,unsigned reason) noexcept
    {
        if(!enabled||at<0)return;auto& p=proposals[at];++p.evalCalls;p.evalValid+=e.valid;
        if(reason&&!p.firstInvalid)p.firstInvalid=reason;
        p.lastObjective=observationBits(e.objective);p.lastHard=observationBits(e.hardDW);
        if(p.evalCalls==1){p.firstObjective=p.lastObjective;p.firstHard=p.lastHard;}
    }
    void search(int at,const Search& s) noexcept
    {
        if(!enabled||at<0)return;auto& p=proposals[at];p.requests=s.requests;p.iterations=s.iterations;p.projectionUpdates=s.projectionUpdates;
        p.improved=s.improved;p.bestHardImproved=s.bestHardImproved;p.requestCap=s.stoppedByRequestCap;p.timeCap=s.stoppedByTimeBudget;
        p.restorationAttempts=s.restorationAttempts;p.restorationRequests=s.restorationRequests;
        if(s.parameters.size()>9||s.bestHardParameters.size()>9){incomplete=true;return;}
        for(unsigned j=0;j<s.parameters.size();++j)p.parameters[j]=observationBits(s.parameters[j]);
        for(unsigned j=0;j<s.bestHardParameters.size();++j)p.bestParameters[j]=observationBits(s.bestHardParameters[j]);
    }
    void stop(int at,unsigned reason) noexcept {if(enabled&&at>=0)proposals[at].breakReason=reason;}
    void row(int at,unsigned checkpoint,double fraction,unsigned called,DWVeto veto,bool hard,
             std::int64_t oldHard,std::int64_t nextHard,const std::uint64_t* point,const DWObservedSummary& after) noexcept
    {
        if(!enabled||at<0)return;++fractionSeen;if(fractionCount==128){incomplete=true;return;}
        auto& r=fractions[fractionCount++];r={};r.proposal=unsigned(at);r.checkpoint=checkpoint;r.fraction=observationBits(fraction);
        r.called=called;r.veto=unsigned(veto);r.hardCalled=hard;r.oldHard=oldHard;r.nextHard=nextHard;r.after=after;
        for(unsigned j=0;j<3*proposals[at].n;++j)r.point[j]=point[j];
    }
    std::string record() const
    {
        std::ostringstream o;
        auto summary=[&](const DWObservedSummary& s)
        {
            o<<"["<<s.valid<<","<<s.cells<<","<<s.faces;
            for(unsigned j=0;j<5;++j)o<<","<<s.bad[j];
            for(unsigned j=0;j<5;++j)o<<","<<s.extreme[j];
            for(unsigned j=0;j<5;++j)o<<","<<s.ids[j];o<<"]";
        };
        o<<"PRIVATE finite DW veto observation: {\"version\":1,\"incomplete\":"<<(incomplete?"true":"false")
         <<",\"seen\":"<<seen<<",\"fractionSeen\":"<<fractionSeen<<",\"proposals\":[";
        for(unsigned i=0;i<count;++i)
        {
            const auto& p=proposals[i];o<<(i?",":"")<<"{\"index\":"<<i<<",\"dimension\":"<<p.dimension<<",\"moving\":[";
            for(unsigned j=0;j<p.n;++j)o<<(j?",":"")<<p.moving[j];o<<"],\"oldPointBits\":[";
            for(unsigned j=0;j<3*p.n;++j)o<<(j?",":"")<<p.oldPoint[j];
            o<<"],\"evalCalls\":"<<p.evalCalls<<",\"evalValid\":"<<p.evalValid<<",\"firstInvalid\":"<<p.firstInvalid
             <<",\"firstObjectiveBits\":"<<p.firstObjective<<",\"lastObjectiveBits\":"<<p.lastObjective
             <<",\"firstHardBits\":"<<p.firstHard<<",\"lastHardBits\":"<<p.lastHard
             <<",\"requests\":"<<p.requests<<",\"iterations\":"<<p.iterations<<",\"projectionUpdates\":"<<p.projectionUpdates
             <<",\"improved\":"<<p.improved<<",\"bestHardImproved\":"<<p.bestHardImproved<<",\"requestCap\":"<<p.requestCap
             <<",\"timeCap\":"<<p.timeCap<<",\"breakReason\":"<<p.breakReason
             <<",\"restorationAttempts\":"<<p.restorationAttempts<<",\"restorationRequests\":"<<p.restorationRequests<<",\"parametersBits\":[";
            for(unsigned j=0;j<p.dimension;++j)o<<(j?",":"")<<p.parameters[j];o<<"],\"bestParametersBits\":[";
            for(unsigned j=0;j<p.dimension;++j)o<<(j?",":"")<<p.bestParameters[j];o<<"],\"before\":";summary(p.before);o<<"}";
        }
        o<<"],\"fractions\":[";
        for(unsigned i=0;i<fractionCount;++i)
        {
            const auto& r=fractions[i];o<<(i?",":"")<<"{\"proposal\":"<<r.proposal<<",\"checkpoint\":"<<r.checkpoint
             <<",\"fractionBits\":"<<r.fraction<<",\"called\":"<<r.called<<",\"veto\":"<<r.veto<<",\"hardCalled\":"<<r.hardCalled
             <<",\"oldHardDW\":";if(r.hardCalled)o<<r.oldHard;else o<<"null";
            o<<",\"nextHardDW\":";if(r.hardCalled)o<<r.nextHard;else o<<"null";o<<",\"pointBits\":[";
            for(unsigned j=0;j<3*proposals[r.proposal].n;++j)o<<(j?",":"")<<r.point[j];o<<"],\"after\":";summary(r.after);o<<"}";
        }
        o<<"]}";const std::string text=o.str();
        if(text.size()<=65535)return text; // Complete serial record+newline <=65536.
        return "PRIVATE finite DW veto observation: {\"version\":1,\"incomplete\":true,\"recordUnavailable\":\"serialized-line-cap\"}";
    }
};
static_assert(sizeof(DWVetoObservation)<=65536,"Private POD stack cap");

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

using Id = std::int64_t;
// A census overrun refuses the whole DW proposal. No partial graph is used.
struct CensusCaps
{
    std::set<Id> seeds,examinedPoints,graphNodes;
    bool blocked=false;
    bool seed(Id id) { return insert(seeds,id,4096); }
    bool point(Id id) { return insert(examinedPoints,id,8192); }
    bool node(Id id) { return insert(graphNodes,id,512); }
private:
    bool insert(std::set<Id>& items,Id id,std::size_t cap)
    {
        if (blocked || id<0) { blocked=true;return false; }
        if (items.count(id)) return true;
        if (items.size()>=cap) { blocked=true;return false; }
        items.insert(id);return true;
    }
};
struct DWPoint
{
    Id id=-1;
    XYZ coordinates{{0,0,0}};
    unsigned valence=0;
    bool eligible=false;
};
inline std::vector<Id> selectInteriorNine
(
    const std::vector<DWPoint>& nodes,
    const std::vector<std::vector<unsigned>>& adjacency
)
{
    const unsigned n=nodes.size();
    if ((n!=3 && n!=4) || adjacency.size()!=n) return {};
    std::set<Id> ids;
    std::vector<std::set<unsigned>> edges(n);
    for (unsigned i=0;i<n;++i)
    {
        if (nodes[i].id<0 || !nodes[i].eligible || !nodes[i].valence
         || !ids.insert(nodes[i].id).second) return {};
        for (double x:nodes[i].coordinates) if (!std::isfinite(x)) return {};
        for (unsigned j=0;j<i;++j)
            if (nodes[i].coordinates==nodes[j].coordinates) return {};
        for (unsigned j:adjacency[i])
            if (j>=n || j==i || !edges[i].insert(j).second) return {};
    }
    for (unsigned i=0;i<n;++i) for (unsigned j:edges[i])
        if (!edges[j].count(i)) return {};
    std::set<unsigned> seen{0};std::vector<unsigned> todo{0};
    while (!todo.empty())
    {
        const unsigned i=todo.back();todo.pop_back();
        for (unsigned j:edges[i]) if (seen.insert(j).second) todo.push_back(j);
    }
    if (seen.size()!=n) return {};
    auto order=[&](unsigned a,unsigned b)
    {
        if (nodes[a].valence!=nodes[b].valence) return nodes[a].valence>nodes[b].valence;
        return nodes[a].coordinates<nodes[b].coordinates;
    };
    if (n==3)
    {
        std::vector<unsigned> sorted{0,1,2};std::sort(sorted.begin(),sorted.end(),order);
        return {nodes[sorted[0]].id,nodes[sorted[1]].id,nodes[sorted[2]].id};
    }
    for (const auto& neighbours:edges) if (neighbours.size()!=2) return {};
    std::vector<unsigned> best;
    std::uint64_t bestValence=0;
    for (unsigned a=0;a<n;++a) for (unsigned b=a+1;b<n;++b)
    {
        if (edges[a].count(b) || edges[a]!=edges[b]) continue;
        std::vector<unsigned> hubs{a,b};std::sort(hubs.begin(),hubs.end(),order);
        const std::uint64_t valence=std::uint64_t(nodes[a].valence)+nodes[b].valence;
        bool choose=best.empty() || valence>bestValence;
        if (!choose && valence==bestValence)
        {
            const std::array<XYZ,2> thisKey{{nodes[hubs[0]].coordinates,nodes[hubs[1]].coordinates}};
            const std::array<XYZ,2> bestKey{{nodes[best[0]].coordinates,nodes[best[1]].coordinates}};
            choose=thisKey<bestKey;
        }
        if (choose) { best=hubs;bestValence=valence; }
    }
    if (best.empty()) return {};
    const auto& neighbours=edges[best[0]];
    const unsigned bridge=*std::min_element(neighbours.begin(),neighbours.end(),
        [&](unsigned a,unsigned b){return nodes[a].coordinates<nodes[b].coordinates;});
    return {nodes[best[0]].id,nodes[best[1]].id,nodes[bridge].id};
}


// Single-point D/W proposals use the same finite kernel as the nine-coordinate
// modes. Eligibility is supplied by the normal topology/source-role caller.
inline std::vector<Id> selectInteriorDWeight
(
    const std::vector<DWPoint>& nodes,
    const std::vector<std::vector<unsigned>>& adjacency
)
{
    if (nodes.size()!=1) return selectInteriorNine(nodes,adjacency);
    if (adjacency.size()!=1 || !adjacency[0].empty()) return {};
    const DWPoint& p=nodes[0];
    if (p.id<0 || !p.eligible || !p.valence) return {};
    for (double x:p.coordinates) if (!std::isfinite(x)) return {};
    return {p.id};
}
// Visit the supported component mode first, then each independent point of a
// non-singleton component. The caller retains full-star/source authorization,
// current-prefix evaluation and global world-geometry ranking for every mode.
// A budget exit returns false; the caller must discard the partial proposal list.
template<class Collect,class Available>
inline bool visitInteriorDWeightModes
(
    const std::vector<DWPoint>& nodes,
    const std::vector<std::vector<unsigned>>& adjacency,
    Collect collect,Available available
)
{
    if (!available()) return false;
    if (nodes.empty() || nodes.size()>512 || adjacency.size()!=nodes.size())
        return true;
    const auto selected=selectInteriorDWeight(nodes,adjacency);
    if (selected.size()==1 || selected.size()==3)
    {
        collect(selected);
        if (!available()) return false;
    }
    if (nodes.size()==1) return true;
    std::vector<unsigned> order;
    std::map<Id,unsigned> multiplicity;
    for (unsigned i=0;i<nodes.size();++i)
    {
        if (!available()) return false;
        ++multiplicity[nodes[i].id];
        if (nodes[i].id<0 || !nodes[i].eligible || !nodes[i].valence) continue;
        bool finiteCoordinates=true;
        for (double x:nodes[i].coordinates)
            if (!std::isfinite(x)) finiteCoordinates=false;
        if (finiteCoordinates) order.push_back(i);
    }
    std::sort(order.begin(),order.end(),[&](unsigned a,unsigned b)
    {
        return nodes[a].coordinates<nodes[b].coordinates;
    });
    for (unsigned k=0;k<order.size();++k)
    {
        if (!available()) return false;
        const unsigned i=order[k];
        // Ambiguous world points or labels are refused, never label tie-broken.
        if (multiplicity[nodes[i].id]!=1
         || (k && nodes[i].coordinates==nodes[order[k-1]].coordinates)
         || (k+1<order.size()
          && nodes[i].coordinates==nodes[order[k+1]].coordinates)) continue;
        const auto single=selectInteriorDWeight({nodes[i]},{{}});
        if (single.size()==1)
        {
            collect(single);
            if (!available()) return false;
        }
    }
    return true;
}
struct DWChoice
{
    std::vector<Id> moving;
    Row geometryKey;
    unsigned coverage=0;
    unsigned dimension=0;
    bool eligible=false;
};
inline std::vector<unsigned> rankDWChoices
(
    const std::vector<DWChoice>& choices,unsigned maximum=256
)
{
    // No caller can enlarge the per-rank fallback limit.
    maximum=std::min(maximum,256u);
    std::vector<unsigned> order;
    for (unsigned i=0;i<choices.size();++i)
    {
        const DWChoice& c=choices[i];
        if (!c.eligible || !c.coverage
         || (c.dimension!=3 && c.dimension!=9)
         || c.moving.size()*3!=c.dimension
         || c.geometryKey.size()!=c.dimension || !finite(c.geometryKey))
            continue;
        std::set<Id> ids;
        bool good=true;
        for (Id p:c.moving) if (p<0 || !ids.insert(p).second) good=false;
        for (std::size_t a=0;a<c.moving.size();++a)
            for (std::size_t b=0;b<a;++b)
                if (std::equal(c.geometryKey.begin()+3*a,
                               c.geometryKey.begin()+3*a+3,
                               c.geometryKey.begin()+3*b)) good=false;
        if (good) order.push_back(i);
    }
    std::sort(order.begin(),order.end(),[&](unsigned a,unsigned b)
    {
        return choices[a].coverage!=choices[b].coverage
            ? choices[a].coverage>choices[b].coverage
            : choices[a].geometryKey<choices[b].geometryKey;
    });
    // Repeated world geometry is ambiguous even if its labels/topology or
    // coverage differ. Do not choose one identity by score or local label.
    std::map<Row,unsigned> multiplicity;
    for (unsigned i:order) ++multiplicity[choices[i].geometryKey];
    std::vector<unsigned> selected;
    for (unsigned i:order)
    {
        if (selected.size()>=maximum) break;
        if (multiplicity[choices[i].geometryKey]==1) selected.push_back(i);
    }
    return selected;
}

// This registry accumulates hard passing roles from every accepted prefix.
// Native final arithmetic must retain each role, even if it was bad initially.
struct PrefixRoles
{
    std::set<Id> cells;
    std::array<std::set<Id>,3> faces; // W,R,S
    bool capture
    (
        const std::vector<Id>& cellIds,const Row& determinant,
        const std::vector<Id>& faceIds,const std::vector<std::array<double,3>>& metrics
    )
    {
        if (cellIds.size()!=determinant.size() || faceIds.size()!=metrics.size()) return false;
        PrefixRoles next(*this);std::set<Id> seenCells,seenFaces;
        for (std::size_t i=0;i<cellIds.size();++i)
        {
            if (cellIds[i]<0 || !seenCells.insert(cellIds[i]).second || !std::isfinite(determinant[i])) return false;
            if (determinant[i]>=.001) next.cells.insert(cellIds[i]);
        }
        for (std::size_t i=0;i<faceIds.size();++i)
        {
            if (faceIds[i]<0 || !seenFaces.insert(faceIds[i]).second) return false;
            for (unsigned k=0;k<3;++k)
            {
                const double value=metrics[i][k],limit=k==0 ? .05 : k==1 ? .01 : 4;
                if (!std::isfinite(value)) return false;
                if (k==2 ? value<=limit : value>=limit) next.faces[k].insert(faceIds[i]);
            }
        }
        *this=std::move(next);return true;
    }
    bool holds
    (
        const std::vector<Id>& cellIds,const Row& determinant,
        const std::vector<Id>& faceIds,const std::vector<std::array<double,3>>& metrics
    ) const
    {
        if (cellIds.size()!=determinant.size() || faceIds.size()!=metrics.size()) return false;
        std::map<Id,double> cd;std::map<Id,std::array<double,3>> fd;
        for (std::size_t i=0;i<cellIds.size();++i)
        {
            if (!std::isfinite(determinant[i]) || !cd.emplace(cellIds[i],determinant[i]).second) return false;
        }
        for (std::size_t i=0;i<faceIds.size();++i)
        {
            for (double value:metrics[i]) if (!std::isfinite(value)) return false;
            if (!fd.emplace(faceIds[i],metrics[i]).second) return false;
        }
        for (Id id:cells) if (!cd.count(id) || cd.at(id)<.001) return false;
        for (unsigned k=0;k<3;++k) for (Id id:faces[k])
        {
            if (!fd.count(id)) return false;
            const double value=fd.at(id)[k],limit=k==0 ? .05 : k==1 ? .01 : 4;
            if (k==2 ? value>limit : value<limit) return false;
        }
        return true;
    }
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
    label budgetStopped=0, requestCapStopped=0, unsupported=0, finalRolledBack=0;
    label dwProposed=0, dwCommitted=0, dwRefused=0;
    label singletonContractRefused=0;
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
        const std::map<label,std::pair<vector,scalar>>& currentCells={},
        const bool jointPrefix=false
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
                if (coreSet.size()>(jointPrefix ? 512u : 128u)) { eligible=false;return; }
            }
        }
        for (label c:coreSet) for (label f:cf[c])
        {
            faceSet.insert(f);
            if (faceSet.size()>(jointPrefix ? 4096u : 2048u)) { eligible=false;return; }
            if (f<nei.size())
            {
                const label other=(own[f]==c ? nei[f] : own[f]);
                if (!coreSet.count(other)) outsideSet.insert(other);
            }
            else if (mesh.boundaryMesh()[mesh.boundaryMesh().patchID(f)].coupled())
            { eligible=false; return; }
        }
        if (outsideSet.size()>(jointPrefix ? 512u : 256u)) { eligible=false;return; }
        std::set<label> allCells=coreSet;
        allCells.insert(outsideSet.begin(),outsideSet.end());
        if (dcCartFiniteRepairKernel::intersectsCellZones(allCells,mesh.cellZones()))
        { eligible=false;return; }
        for (label c:outsideSet) for (label f:cf[c])
        {
            faceSet.insert(f);
            if (faceSet.size()>(jointPrefix ? 4096u : 2048u)) { eligible=false;return; }
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


inline dcCartFiniteRepairKernel::DWObservedSummary observeDWState(const Stencil& stencil,const State& state) noexcept
{
    dcCartFiniteRepairKernel::DWObservedSummary out{};if(!state.valid)return out;
    out.valid=1;out.cells=state.determinant.size();
    double extremes[5]={std::numeric_limits<double>::max(),std::numeric_limits<double>::max(),std::numeric_limits<double>::max(),
        -std::numeric_limits<double>::max(),-std::numeric_limits<double>::max()};
    for(unsigned k=0;k<5;++k)out.ids[k]=-1;
    for(unsigned c=0;c<state.determinant.size();++c)
    {
        const double v=state.determinant[c];out.bad[0]+=v<.001;
        if(v<extremes[0]){extremes[0]=v;out.ids[0]=stencil.cells[c].id;}
    }
    for(unsigned f=0;f<stencil.faces.size();++f)if(!stencil.faces[f].coupled)
    {
        ++out.faces;
        for(unsigned k=0;k<4;++k)
        {
            const double v=state.faceQuality[f][k],limit=k==0?.05:k==1?.01:k==2?4:70;
            out.bad[k+1]+=k<2?v<limit:v>limit;
            if(k<2?v<extremes[k+1]:v>extremes[k+1]){extremes[k+1]=v;out.ids[k+1]=stencil.faces[f].id;}
        }
    }
    for(unsigned k=0;k<5;++k)out.extreme[k]=dcCartFiniteRepairKernel::observationBits(extremes[k]);
    return out; // Full complete-stencil D and noncoupled W/R/S/N; diagnostic only.
}
inline bool observeDWPoints(const Stencil& stencil,const std::vector<label>& moving,
    const pointField& points,std::uint64_t (&bits)[9]) noexcept
{
    if(moving.empty()||moving.size()>3)return false;
    for(unsigned i=0;i<moving.size();++i)
    {
        const auto p=stencil.pointIndex.find(moving[i]);if(p==stencil.pointIndex.end())return false;
        for(direction k=0;k<3;++k)bits[3*i+k]=dcCartFiniteRepairKernel::observationBits(points[p->second][k]);
    }
    return true;
}

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


struct ProposalMode
{
    dcCartFiniteRepairKernel::ObjectiveKind kind=dcCartFiniteRepairKernel::ObjectiveKind::Ratio;
    dcCartFiniteRepairKernel::Mode ratio;
    std::vector<label> moving;
    unsigned dimension=0;
    label target=-1;
};

inline bool installedBinding(const Stencil& stencil)
{
    if (!stencil.eligible) return false;
    const State compact=stencil.evaluate(stencil.original);
    const State installed=stencil.evaluate(stencil.original,true);
    if (!compact.valid || !installed.valid) return false;
    for (label f=0;f<label(stencil.faces.size());++f)
        if (!sameVector(compact.sf[f],installed.sf[f])
         || !sameVector(compact.fc[f],installed.fc[f])) return false;
    for (label c=0;c<label(stencil.cells.size());++c)
        if (!sameVector(compact.cc[c],installed.cc[c])
         || !sameScalar(compact.volume[c],installed.volume[c])) return false;
    return true;
}

inline bool prefixRoles
(
    dcCartFiniteRepairKernel::PrefixRoles& roles,
    const Stencil& stencil,const State& state,bool capture
)
{
    std::vector<dcCartFiniteRepairKernel::Id> cellIds,faceIds;
    dcCartFiniteRepairKernel::Row determinant;
    std::vector<std::array<double,3>> metrics;
    for (label c=0;c<label(stencil.cells.size());++c)
    { cellIds.push_back(stencil.cells[c].id);determinant.push_back(state.determinant[c]); }
    for (label f=0;f<label(stencil.faces.size());++f) if (!stencil.faces[f].coupled)
    {
        faceIds.push_back(stencil.faces[f].id);
        metrics.push_back({{state.faceQuality[f][0],state.faceQuality[f][1],state.faceQuality[f][2]}});
    }
    return capture ? roles.capture(cellIds,determinant,faceIds,metrics)
        : roles.holds(cellIds,determinant,faceIds,metrics);
}

inline label hardDWCount(const Stencil& stencil,const State& state)
{
    label count=0;
    for (scalar d:state.determinant) count+=d<.001;
    for (label f=0;f<label(stencil.faces.size());++f) if (!stencil.faces[f].coupled)
        count+=state.faceQuality[f][0]<.05;
    return count;
}

// The census uses earliest installed fields only to propose graph components.
// Each selected component is subsequently rebound to the earliest installed
// oracle, then assessed against workingPoints/currentCells (the accepted prefix).
inline std::vector<ProposalMode> selectDWModes
(
    const polyMesh& mesh,const pointField& points,
    const std::vector<unsigned char>& protectedFace,
    const std::vector<unsigned char>& protectedPoint,
    const std::map<label,std::pair<vector,scalar>>& currentCells,
    const std::set<label>& changedPoints,
    const std::function<bool()>& available,
    const std::function<bool(const std::vector<label>&)>& singletonSourceRoleContract,
    label& singletonContractRefused
)
{
    std::vector<ProposalMode> out;
    const auto& cf=mesh.cells();const auto& pf=mesh.pointFaces();
    const auto& own=mesh.faceOwner();const auto& nei=mesh.faceNeighbour();
    const auto& sf=mesh.faceAreas();const auto& fc=mesh.faceCentres();
    const auto& cc=mesh.cellCentres();const auto& vol=mesh.cellVolumes();
    dcCartFiniteRepairKernel::CensusCaps census;
    const auto& seeds=census.seeds;
    std::set<label> badCells,badFaces;
    std::size_t visits=0;
    auto withinClock=[&]()
    { return (++visits&255)!=0 || available(); };
    // No label list/trace/case identity participates in the census.
    for (label c=0;c<mesh.nCells();++c)
    {
        if ((c&1023)==0 && !available()) return out;
        scalar area=0;label count=0;
        for (label f:cf[c])
        {
            if (!withinClock()) return out;
            const bool selected=f<nei.size()
                || mesh.boundaryMesh()[mesh.boundaryMesh().patchID(f)].coupled();
            if (selected) { area+=mag(sf[f]);++count; }
        }
        if (!finiteScalar(area) || !count || area<ROOTVSMALL) continue;
        const scalar average=area/count;symmTensor tensor(Zero);
        for (label f:cf[c])
        {
            if (!withinClock()) return out;
            const bool selected=f<nei.size()
                || mesh.boundaryMesh()[mesh.boundaryMesh().patchID(f)].coupled();
            if (selected) tensor+=sqr(sf[f]/average);
        }
        const scalar d=mag(det(tensor))/8.0;
        if (finiteScalar(d) && d<.001)
        { if (!census.seed(c)) return out;badCells.insert(c); }
    }
    for (label f=0;f<nei.size();++f)
    {
        if ((f&4095)==0 && !available()) return out;
        const scalar a=mag(sf[f] & (fc[f]-cc[own[f]]));
        const scalar b=mag(sf[f] & (cc[nei[f]]-fc[f]));
        const scalar weight=min(a,b)/(a+b+VSMALL);
        if (finiteScalar(weight) && weight<.05)
        {
            if (!census.seed(own[f]) || !census.seed(nei[f])) return out;
            badFaces.insert(f);
        }
        const scalar ratio=min(vol[own[f]],vol[nei[f]])
            /(max(vol[own[f]],vol[nei[f]])+VSMALL);
        if (finiteScalar(ratio) && ratio<.01)
        { if (!census.seed(own[f]) || !census.seed(nei[f])) return out; }
    }
    std::map<label,bool> eligibility;
    auto eligible=[&](label p)
    {
        const auto cached=eligibility.find(p);
        if (cached!=eligibility.end()) return cached->second;
        if (!census.point(p)) return false;
        bool good=p>=0 && p<points.size() && !protectedPoint[p]
            && finiteVector(points[p]) && !pf[p].empty() && pf[p].size()<=512;
        std::set<label> core;
        if (good) for (label f:pf[p])
        {
            if (!withinClock()) { census.blocked=true;return false; }
            if (protectedFace[f] || f>=nei.size() || mesh.faces()[f].size()!=3)
            { good=false;break; }
            core.insert(own[f]);core.insert(nei[f]);
            if (core.size()>128) { good=false;break; }
        }
        // Reject a point whose core owns any coupled face before graph
        // assembly; otherwise an ineligible hub could mask a valid component.
        if (good) for (label c:core)
        {
            for (label f:cf[c])
            {
                if (!withinClock()) { census.blocked=true;return false; }
                if (f>=nei.size()
                 && mesh.boundaryMesh()[mesh.boundaryMesh().patchID(f)].coupled())
                { good=false;break; }
            }
            if (!good) break;
        }
        eligibility[p]=good;return good;
    };
    std::map<label,std::set<label>> adjacency;
    for (label c:seeds)
    {
        if (!available()) return out;
        std::set<label> nodes;
        for (label f:cf[c]) for (label p:mesh.faces()[f])
        {
            if (!withinClock()) return out;
            if (eligible(p))
            {
                if (!census.node(p)) return out;
                nodes.insert(p);
            }
            if (census.blocked) return out;
        }
        // Each eligible point is present even if this cell supplies no edge.
        for (label p:nodes) adjacency[p];
        for (label a:nodes) for (label b:nodes)
        {
            if (!withinClock()) return out;
            if (a!=b) adjacency[a].insert(b);
        }
    }
    // Adjacency is co-occurrence in an actual bad-element seed cell;
    // opposite sides of a face are not connected without a shared cell.
    std::set<label> visited;
    std::vector<ProposalMode> candidates;
    std::vector<dcCartFiniteRepairKernel::DWChoice> choices;
    for (const auto& entry:adjacency)
    {
        if (visited.count(entry.first)) continue;
        if (!available()) return out;
        std::vector<label> component,todo{entry.first};
        visited.insert(entry.first);
        while (!todo.empty())
        {
            if ((component.size()&255)==0 && !available()) return out;
            const label p=todo.back();todo.pop_back();component.push_back(p);
            for (label q:adjacency.at(p)) if (visited.insert(q).second) todo.push_back(q);
        }
        std::map<label,unsigned> local;
        for (unsigned i=0;i<component.size();++i) local[component[i]]=i;
        std::vector<dcCartFiniteRepairKernel::DWPoint> nodes;
        std::vector<std::vector<unsigned>> graph(component.size());
        for (unsigned i=0;i<component.size();++i)
        {
            const label p=component[i];
            nodes.push_back({p,{{points[p].x(),points[p].y(),points[p].z()}},
                unsigned(pf[p].size()),eligible(p)});
            for (label q:adjacency.at(p)) graph[i].push_back(local.at(q));
        }
        auto collectMode=[&](const std::vector<dcCartFiniteRepairKernel::Id>& selected)
        {
            if (selected.size()!=1 && selected.size()!=3) return;
            ProposalMode mode;mode.kind=dcCartFiniteRepairKernel::ObjectiveKind::DWeightCount;
            mode.dimension=3*selected.size();
            for (auto p:selected) mode.moving.push_back(label(p));
            if (mode.dimension==3 && (!singletonSourceRoleContract
             || !singletonSourceRoleContract(mode.moving)))
            { ++singletonContractRefused;return; }
            // Cap prefilter precedes rank-local priority: an unsupported 131-cell
            // component cannot shadow a valid 71/73-cell component.
            Stencil stencil(mesh,points,mode.moving,currentCells);
            if (!stencil.eligible || !available()) return;
            std::set<label> jointMoving(changedPoints);
            jointMoving.insert(mode.moving.begin(),mode.moving.end());
            const Stencil joint(mesh,points,
                std::vector<label>(jointMoving.begin(),jointMoving.end()),currentCells,true);
            if (!joint.eligible || !available()) return;
            // Ranking observes the current accepted prefix, not stale installed
            // fields. The full census above is only a bounded topology seed pool.
            const State current=stencil.evaluate(stencil.original);
            if (!current.valid || !available()) return;
            unsigned score=0;
            std::set<label> active;
            for (label c:stencil.core)
            {
                score+=current.determinant[c]<.001;
                for (label f:stencil.cells[c].faces) active.insert(f);
            }
            for (label f:active) if (!stencil.faces[f].coupled)
                score+=current.faceQuality[f][0]<.05;
            if (!score) return;
            dcCartFiniteRepairKernel::DWChoice choice;
            choice.dimension=mode.dimension;choice.coverage=score;choice.eligible=true;
            for (label p:mode.moving)
            {
                choice.moving.push_back(p);
                for (direction k=0;k<3;++k) choice.geometryKey.push_back(points[p][k]);
            }
            candidates.push_back(mode);choices.push_back(choice);
        };
        if (!dcCartFiniteRepairKernel::visitInteriorDWeightModes
            (nodes,graph,collectMode,available)) return out;
    }
    if (!available()) return out; // No partially ranked list after census budget exit.
    const auto order=dcCartFiniteRepairKernel::rankDWChoices(choices,256);
    for (unsigned chosen:order) out.push_back(candidates[chosen]);
    return out;
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
    polyMesh& mesh,const std::function<scalar()>& remainingStageBudget,
    const std::function<bool(const std::vector<label>&)>& singletonSourceRoleContract={}
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
    dcCartFiniteRepairKernel::DWVetoObservation observation;
    const char* observationFlag=std::getenv("DCCART_PRIVATE_DW_VETO_OBSERVE");
    observation.enabled=!Pstream::parRun() && observationFlag && std::strcmp(observationFlag,"1")==0;
    const bool widerDWeightSearch=dcCartFiniteRepairKernel::widerDWeightSearchEnabled
        (std::getenv("DCCART_PRIVATE_DW_WIDER_SEARCH"),Pstream::parRun());
    const bool nonlinearFeasibilityTail=dcCartFiniteRepairKernel::widerDWeightSearchEnabled
        (std::getenv("DCCART_PRIVATE_DW_NONLINEAR_FEASIBILITY_TAIL"),Pstream::parRun());
    const bool residualDWeightReselection=dcCartFiniteRepairKernel::widerDWeightSearchEnabled
        (std::getenv("DCCART_PRIVATE_DW_RESIDUAL_RESELECT"),Pstream::parRun());
    if (widerDWeightSearch)
        Pout<< "PRIVATE wider DW search policy: trustBox=.2 radiusFactor=5"
            << " requestsCap=256 iterationsCap=32 stageCap=20; actual guards retained" << nl;

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
    dcCartFiniteRepairKernel::PrefixRoles acceptedRoles;
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

    auto attempt=[&](const ProposalMode& proposal)
    {
        const bool dw=proposal.kind==dcCartFiniteRepairKernel::ObjectiveKind::DWeightCount;
        const auto& mode=proposal.ratio;
        const auto& moving=proposal.moving;
        if (!localReady || !available()) { ++result.budgetStopped;return; }
        // Source-role authorization is independent of actual zone emptiness.
        // The existing two-argument caller retains R/nine only; absent/unknown
        // normal source semantics never enables new singleton movement.
        if (dw && proposal.dimension==3 && (!singletonSourceRoleContract
         || !singletonSourceRoleContract(moving)))
        { ++result.singletonContractRefused;return; }
        // Recheck the complete union after earlier candidates have committed.
        // A list-build cap alone cannot authorize a growing accepted prefix.
        std::set<label> trialMoving(changedPoints);
        trialMoving.insert(moving.begin(),moving.end());
        const Stencil candidateJoint(mesh,workingPoints,
            std::vector<label>(trialMoving.begin(),trialMoving.end()),currentCells,true);
        if (!available()) { ++result.budgetStopped;return; }
        if (!candidateJoint.eligible) { ++result.unsupported;return; }

        // Earliest binding always uses originalPoints and original halo. A
        // previously accepted R prefix has not yet been installed in mesh.
        const Stencil earliest(mesh,originalPoints,moving);
        if (!available()) { ++result.budgetStopped;return; }
        if (!installedBinding(earliest)) { ++result.unsupported;return; }
        Stencil stencil(mesh,workingPoints,moving,currentCells);
        if (!available()) { ++result.budgetStopped;return; }
        if (!stencil.eligible) { ++result.unsupported;return; }
        const pointField initial(stencil.original);
        const State before=stencil.evaluate(initial);
        if (!before.valid) { ++result.unsupported;return; }
        const label targetIndex=dw ? -1 : stencil.faceIndex.at(proposal.target);
        if (!dw && before.faceQuality[targetIndex][1]>=.01) return;
        if (dw && !hardDWCount(stencil,before)) return;
        auto coordinates=[&](const dcCartFiniteRepairKernel::Row& q,scalar fraction=1)
        {
            pointField p(initial);
            if (dw)
            {
                for (unsigned i=0;i<moving.size();++i) for (direction k=0;k<3;++k)
                    p[stencil.pointIndex.at(moving[i])][k]+=fraction*q[3*i+k]*stencil.scale;
            }
            else
            {
                for (direction k=0;k<3;++k)
                    p[stencil.pointIndex.at(mode.pivot)][k]+=fraction*q[k]*stencil.scale;
                if (proposal.dimension==4)
                {
                    const scalar delta=fraction*q[3]*stencil.scale;
                    p[stencil.pointIndex.at(mode.pairA)][mode.axis]+=delta;
                    p[stencil.pointIndex.at(mode.pairB)][mode.axis]+=delta;
                }
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
        int observationIndex=-1;
        auto evaluate=[&](const dcCartFiniteRepairKernel::Row& q)
        {
            dcCartFiniteRepairKernel::Evaluation value;
            if (q.size()!=proposal.dimension || !dcCartFiniteRepairKernel::finite(q))
            {if(observation.enabled&&dw)observation.evaluation(observationIndex,value,1);return value;}
            const State next=stencil.evaluate(coordinates(q));
            if (!next.valid)
            {if(observation.enabled&&dw)observation.evaluation(observationIndex,value,2);return value;}
            if (!dw) value.objective=-next.faceQuality[targetIndex][1]/.01;
            else
            {
                value.hardDW=hardDWCount(stencil,next);
                value.objective=0;
                for (label c:stencil.core) value.objective+=std::exp(-next.determinant[c]/.001);
                for (label f:activeFaces) if (!stencil.faces[f].coupled)
                    value.objective+=.25*std::exp(-next.faceQuality[f][0]/.05)
                        +.1*std::exp(-next.faceQuality[f][1]/.01);
            }
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
            if(observation.enabled&&dw)observation.evaluation(observationIndex,value,value.valid?0:3);
            return value;
        };
        ++result.proposed;if (dw) ++result.dwProposed;
        if(observation.enabled&&dw)
        {
            std::int64_t ids[3]{};std::uint64_t point[9]{};
            if(moving.size()<=3&&observeDWPoints(stencil,moving,initial,point))
            {
                for(unsigned i=0;i<moving.size();++i)ids[i]=moving[i];
                observationIndex=observation.open(ids,moving.size(),proposal.dimension,point,observeDWState(stencil,before));
            }
            else observation.incomplete=true;
        }
        const auto search=dcCartFiniteRepairKernel::propose
            (proposal.dimension,evaluate,available,proposal.kind,dw && widerDWeightSearch,
             dw && nonlinearFeasibilityTail);
        if(observation.enabled&&dw)observation.search(observationIndex,search);
        result.requests+=search.requests;
        if (search.stoppedByRequestCap) ++result.requestCapStopped;
        if (search.stoppedByTimeBudget) ++result.budgetStopped;
        bool accepted=false;
        // The last soft iterate is always primary. Only after all its fractions
        // fail may one distinct best-hard checkpoint use the identical guards.
        for (unsigned checkpoint=0;checkpoint<2 && !accepted;++checkpoint)
        {
            if (checkpoint && (!dw || !search.bestHardImproved
             || search.bestHardParameters==search.parameters)) break;
            const auto& parameters=checkpoint ? search.bestHardParameters : search.parameters;
            for (scalar fraction:{scalar(1),scalar(.95),scalar(.9),scalar(.5)})
            {
            if(observation.enabled&&dw)
            {
                if(!search.improved){observation.stop(observationIndex,1);break;}
                if(!available()){observation.stop(observationIndex,2);break;}
            }
            else if (!search.improved || !available()) break;
            const pointField candidate=coordinates(parameters,fraction);
            const State after=stencil.evaluate(candidate);
            if(observation.enabled&&dw)
            {
                unsigned called=0;bool hardCalled=false;std::int64_t oldHard=0,nextHard=0;
                auto hardValue=[&](label value,bool old)
                {hardCalled=true;if(old)oldHard=value;else nextHard=value;return value;};
                const auto veto=dcCartFiniteRepairKernel::firstDWVeto(called,
                    [&](){return !after.valid;},
                    [&](){return !dw && after.faceQuality[targetIndex][1]<.01;},
                    [&](){return dw && hardValue(hardDWCount(stencil,after),false)>=hardValue(hardDWCount(stencil,before),true);},
                    [&](){return !qualityLineage(stencil,before,after,ceiling);},
                    [&](){return !geometricGuard(stencil,before,after,candidate);},
                    [&](){return !pointClearance(workingPoints,moving,candidate,stencil,available);},
                    [&](){return !prefixRoles(acceptedRoles,stencil,after,true);});
                std::uint64_t point[9]{};
                if(observeDWPoints(stencil,moving,candidate,point))
                    observation.row(observationIndex,checkpoint,fraction,called,veto,hardCalled,oldHard,nextHard,point,observeDWState(stencil,after));
                else observation.incomplete=true;
                if(veto!=dcCartFiniteRepairKernel::DWVeto::None)continue;
            }
            else
            {
            if (!after.valid
             || (!dw && after.faceQuality[targetIndex][1]<.01)
             || (dw && hardDWCount(stencil,after)>=hardDWCount(stencil,before))
             || !qualityLineage(stencil,before,after,ceiling)
             || !geometricGuard(stencil,before,after,candidate)
             || !pointClearance(workingPoints,moving,candidate,stencil,available)) continue;
            // Accumulate all newly passing hard roles before replacing the
            // local prefix. The final installed oracle must retain them.
            if (!prefixRoles(acceptedRoles,stencil,after,true)) continue;
            }
            for (label p:moving)
            { workingPoints[p]=candidate[stencil.pointIndex.at(p)];changedPoints.insert(p); }
            for (label c:stencil.core)
                currentCells[stencil.cells[c].id]={after.cc[c],after.volume[c]};
            ++result.committed;if (dw) ++result.dwCommitted;accepted=true;
            Pout<< "dcCart finite point proposal: kind=" << (dw ? "DW" : "R")
                << " target=" << proposal.target << " dimension=" << proposal.dimension
                << " moving=";
            for (label p:moving) Pout<< p << ',';
            Pout<< " fraction=" << fraction
                << " oldHardDW=" << hardDWCount(stencil,before)
                << " proposedHardDW=" << hardDWCount(stencil,after);
            if (!dw) Pout<< " oldRatio=" << before.faceQuality[targetIndex][1]
                << " proposedRatio=" << after.faceQuality[targetIndex][1];
            Pout<< " core=" << stencil.core.size() << " outside=" << stencil.outside.size()
                << " faces=" << stencil.faces.size() << " points=" << stencil.pointIDs.size()
                << " requests=" << search.requests << " checkpoint=" << checkpoint << nl;
            break;
            }
        }
        if (!accepted) { ++result.refused;if (dw) ++result.dwRefused; }
    };
    // R proposals precede DW and use the same finite search/actual guards.
    for (label target:targets)
    {
        if (!localReady || !available()) { ++result.budgetStopped;break; }
        const auto ratio=selectMode(mesh,workingPoints,target,protectedFace,protectedPoint);
        if (!ratio.dimension) { ++result.unsupported;continue; }
        ProposalMode mode;mode.ratio=ratio;mode.dimension=ratio.dimension;mode.target=target;
        mode.moving.push_back(ratio.pivot);
        if (ratio.dimension==4) { mode.moving.push_back(ratio.pairA);mode.moving.push_back(ratio.pairB); }
        attempt(mode);
    }
    // The installed census remains the original topology seed pool. An exact1,
    // serial-only opt-in may re-rank it once on the accepted current prefix.
    // Both rounds share 256 admissions and the original 20-second clock.
    if (localReady && available())
    {
        unsigned dwAdmissions=0;
        const label commitsBeforeFirstDW=result.dwCommitted;
        auto runDWRound=[&]()
        {
            const auto modes=selectDWModes
            (
                mesh,workingPoints,protectedFace,protectedPoint,currentCells,
                changedPoints,available,singletonSourceRoleContract,
                result.singletonContractRefused
            );
            if (modes.empty()) ++result.unsupported;
            for (const ProposalMode& mode:modes)
            {
                if (dwAdmissions>=256) break;
                if (!available()) { ++result.budgetStopped;break; }
                ++dwAdmissions;
                attempt(mode);
            }
        };
        runDWRound();
        if (residualDWeightReselection && result.dwCommitted>commitsBeforeFirstDW
         && dwAdmissions<256 && available()) runDWRound();
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
        Stencil joint(mesh,originalPoints,moving,{},true);
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
                 || !geometricGuard(joint,before,after,actual)
                 || !prefixRoles(acceptedRoles,joint,after,false)) failure=1;
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
            result.finalRolledBack=result.committed;result.committed=0;result.dwCommitted=0;
        }
    }
    result.elapsedSeconds=seconds();
    Pout<< "dcCart finite point repair: proposed=" << result.proposed
        << " committed=" << result.committed << " refused=" << result.refused
        << " unsupported=" << result.unsupported << " requests=" << result.requests
        << " budgetStopped=" << result.budgetStopped
        << " requestCapStopped=" << result.requestCapStopped
        << " dwProposed=" << result.dwProposed << " dwCommitted=" << result.dwCommitted
        << " dwRefused=" << result.dwRefused
        << " singletonContractRefused=" << result.singletonContractRefused << " rolledBack=" << result.finalRolledBack
        << " setup=" << result.setupSeconds << " proposal=" << result.proposalSeconds
        << " installed=" << result.installedSeconds << " guard=" << result.guardSeconds
        << " rollback=" << result.rollbackSeconds << " total=" << result.elapsedSeconds
        << " cap=20 ratioComponentCap=8 dwProposalCap=256 jointCoreCap=512 jointOutsideCap=512 jointFaceCap=4096 requestCapPerProposal=256" << nl;
    if(observation.enabled)
    {
        // Printing follows all original decisions/collectives/timing. It is
        // diagnostic overhead; no time-neutral scheduling claim is made.
        try {const std::string text=observation.record();Pout<<text.c_str()<<nl;}
        catch(...){Pout<<"PRIVATE finite DW veto observation: {\"version\":1,\"incomplete\":true,\"recordUnavailable\":\"serialization-exception\"}"<<nl;}
    }
    return result;
}
} // namespace dcCartFinitePointRepair
} // namespace Foam
#endif // production adapter
#endif // dcCartFinitePointRepair_H
