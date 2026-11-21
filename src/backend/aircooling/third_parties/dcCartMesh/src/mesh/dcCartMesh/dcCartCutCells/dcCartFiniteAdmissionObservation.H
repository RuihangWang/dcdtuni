#ifndef dcCartFiniteAdmissionObservation_H
#define dcCartFiniteAdmissionObservation_H
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <sstream>
#include <string>
namespace Foam { namespace dcCartFiniteAdmissionObservation
{
// Diagnostic only. Never samples a clock/callback or grants source permission.
enum class Phase : unsigned { initial, pointScan, graph, ratioCensus, ratioTrials, dwCensus, dwTrials, final, count };
inline const char* name(Phase p) noexcept
{
    switch(p)
    {
        case Phase::initial:return "initial";case Phase::pointScan:return "point-scan";
        case Phase::graph:return "graph";case Phase::ratioCensus:return "ratio-census";
        case Phase::ratioTrials:return "ratio-trials";case Phase::dwCensus:return "dw-census";
        case Phase::dwTrials:return "dw-trials";case Phase::final:return "final";
        default:return "unknown";
    }
}
inline bool enabled(const char* flag) noexcept { return flag && std::strcmp(flag,"1")==0; }
struct Record
{
    const bool on;
    Phase phase=Phase::initial,firstBudgetPhase=Phase::initial;
    std::array<unsigned, unsigned(Phase::count)> samples{},passed{};
    bool haveBudgetRefusal=false,firstStageSampled=false,validCeiling=false;
    bool dimensionsValid=true,hasSingletonContract=false;
    unsigned pointScanState=0; // unchecked, partial, finite, nonfinite
    bool setupLocalReady=false,dwCensusEntered=false;
    double ceiling=0,firstExternal=0,firstStage=0,firstAllowance=0;
    unsigned dwModes=0,dwAttempts=0,dwSearches=0;
    const char* firstPreSearchRefusal="none";
    explicit Record(const char* flag):on(enabled(flag)){}
    static void add(unsigned& n) noexcept { if(n<65535)++n; }
    void setPhase(Phase next) noexcept { if(on)phase=next; }
    void sample(double external,bool stageSampled,double stage,double allowance) noexcept
    {
        if(!on)return;
        add(samples[unsigned(phase)]);
        if(allowance>3){add(passed[unsigned(phase)]);return;}
        if(haveBudgetRefusal)return;
        haveBudgetRefusal=true;firstBudgetPhase=phase;firstExternal=external;
        firstStageSampled=stageSampled;firstStage=stage;firstAllowance=allowance;
    }
    void setCeiling(double value,bool valid) noexcept { if(on){ceiling=value;validCeiling=valid;} }
    void badDimension() noexcept { if(on)dimensionsValid=false; }
    void beginPointScan() noexcept { if(on)pointScanState=1; }
    void finishPointScan() noexcept { if(on)pointScanState=2; }
    void badPoint() noexcept { if(on)pointScanState=3; }
    void setSetup(bool ready) noexcept { if(on)setupLocalReady=ready; }
    void enterDW(unsigned modes) noexcept { if(on){dwCensusEntered=true;dwModes=modes;} }
    void beginDWCensus() noexcept { if(on)dwCensusEntered=true; }
    void attemptDW() noexcept { if(on)add(dwAttempts); }
    void searchDW() noexcept { if(on)add(dwSearches); }
    void refuseProposal(const char* why) noexcept
    { if(on && std::strcmp(firstPreSearchRefusal,"none")==0)firstPreSearchRefusal=why; }
    static void number(std::ostream& out,double value)
    { if(std::isfinite(value))out<<std::setprecision(17)<<value;else out<<"null"; }
    std::string json(int rank)const
    {
        if(!on)return {};
        std::ostringstream out;
        out<<"{\"schema\":\"dctwin-finite-admission-observation/v1\",\"rank\":"<<rank
           <<",\"scope\":\"observation-only\",\"validCeiling\":"<<(validCeiling?"true":"false")<<",\"ceiling\":";
        number(out,ceiling);
        out<<",\"dimensionsValid\":"<<(dimensionsValid?"true":"false")
           <<",\"pointScanState\":\""<<(pointScanState==0?"unchecked":pointScanState==1?"partial":pointScanState==2?"finite":"nonfinite")<<"\""
           <<",\"hasSingletonContract\":"<<(hasSingletonContract?"true":"false")
           <<",\"setupLocalReady\":"<<(setupLocalReady?"true":"false")
           <<",\"dwCensusEntered\":"<<(dwCensusEntered?"true":"false")
           <<",\"dwModes\":"<<dwModes<<",\"dwAttempts\":"<<dwAttempts<<",\"dwSearches\":"<<dwSearches
           <<",\"firstPreSearchRefusal\":\""<<firstPreSearchRefusal<<"\",\"firstBudgetRefusal\":";
        if(!haveBudgetRefusal)out<<"null";
        else
        {
            out<<"{\"phase\":\""<<name(firstBudgetPhase)<<"\",\"external\":";number(out,firstExternal);
            out<<",\"stageSampled\":"<<(firstStageSampled?"true":"false")<<",\"stageSeconds\":";
            if(firstStageSampled)number(out,firstStage);else out<<"null";
            out<<",\"allowance\":";number(out,firstAllowance);out<<'}';
        }
        out<<",\"availableSamplesByPhase\":[";
        for(unsigned i=0;i<unsigned(Phase::count);++i){if(i)out<<',';out<<samples[i];}
        out<<"],\"availablePassesByPhase\":[";
        for(unsigned i=0;i<unsigned(Phase::count);++i){if(i)out<<',';out<<passed[i];}
        out<<"]}";return out.str();
    }
};
// Captures only the application's already sampled whole-job clock.
// The first observed allowance at/below the admission margin is not, by
// itself, proof of a final installed-guard refusal.
struct JobRecord
{
    const bool on;bool observed=false;
    double limit=0,elapsed=0,allowance=0;
    explicit JobRecord(const char* flag):on(enabled(flag)){}
    bool sample(double runLimit,double runElapsed,double allowed) noexcept
    {
        if(!on||observed||allowed>3)return false;
        observed=true;limit=runLimit;elapsed=runElapsed;allowance=allowed;return true;
    }
    std::string json(int rank)const
    {
        if(!on||!observed)return {};
        std::ostringstream out;
        out<<"{\"schema\":\"dctwin-finite-job-admission-observation/v1\",\"rank\":"<<rank
           <<",\"scope\":\"observation-only\",\"runLimit\":";Record::number(out,limit);
        out<<",\"runElapsed\":";Record::number(out,elapsed);
        out<<",\"externalAllowance\":";Record::number(out,allowance);
        out<<",\"downstreamReserve\":60,\"stageCap\":20,\"admissionMargin\":3}";
        return out.str();
    }
};
} }
#endif
