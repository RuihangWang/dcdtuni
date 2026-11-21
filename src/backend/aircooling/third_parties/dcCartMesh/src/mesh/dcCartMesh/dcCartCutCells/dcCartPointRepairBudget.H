// Optional native repair reserves time for the remaining product pipeline.
#ifndef dcCartPointRepairBudget_H
#define dcCartPointRepairBudget_H

#include <algorithm>
#include <cmath>
#include <cstring>

namespace Foam
{
namespace dcCartPointRepairBudget
{
constexpr double stageCap = 20;
constexpr double downstreamReserve = 60;
inline bool enabled(const char* flag)
{
    return flag && std::strcmp(flag, "1") == 0;
}

inline double allowance(double runLimitSeconds, double runElapsedSeconds)
{
    if (!std::isfinite(runLimitSeconds) || runLimitSeconds < 0
     || !std::isfinite(runElapsedSeconds) || runElapsedSeconds < 0)
    {
        return 0;
    }
    if (runLimitSeconds == 0) { return stageCap; }
    return std::min(stageCap,
        std::max(0.0, runLimitSeconds-runElapsedSeconds-downstreamReserve));
}

// The optional raw/cavity clock leaves the existing finite-phase cap and its
// downstream reserve on the same original job clock. Disabled keeps 40 s.
inline double upstreamAllowance(double runLimitSeconds, double runElapsedSeconds,
    bool finitePhaseEnabled)
{
    if (!std::isfinite(runLimitSeconds) || runLimitSeconds <= 0
     || !std::isfinite(runElapsedSeconds) || runElapsedSeconds < 0)
        return 0;
    const double reserve=finitePhaseEnabled ? stageCap+downstreamReserve : 40;
    return std::max(0.0,runLimitSeconds-runElapsedSeconds-reserve);
}

inline bool optionalAvailable(bool hardAvailable, double upstreamSeconds)
{
    return hardAvailable && std::isfinite(upstreamSeconds) && upstreamSeconds>0;
}
}
}
#endif
